"""Read-only route status tests; runnable without Home Assistant installed."""

import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "mikrotik_router"


def load_routes():
    spec = importlib.util.spec_from_file_location(
        "route_status", PACKAGE / "route_status.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RouteStatusTest(unittest.TestCase):
    def test_ipv4_routes_have_distinct_ids_and_actual_active_state(self):
        rows = [
            {
                ".id": "*A",
                "dst-address": "0.0.0.0/0",
                "routing-table": "usa",
                "gateway": "nord-wg",
                "immediate-gw": "nord-wg",
                "comment": "Nord USA",
                "active": True,
                "disabled": False,
                "dynamic": False,
            },
            {
                ".id": "*B",
                "dst-address": "0.0.0.0/0",
                "routing-table": "usa",
                "gateway": "nord-wg",
                "active": False,
                "disabled": False,
                "dynamic": False,
            },
            {
                ".id": "*C",
                "dst-address": "10.0.0.0/8",
                "active": True,
                "disabled": False,
                "dynamic": True,
            },
        ]
        result = load_routes().parse_routes(rows)
        self.assertEqual(set(result), {"*A", "*B", "*C"})
        self.assertTrue(result["*A"]["active"])
        self.assertFalse(result["*B"]["active"])
        self.assertNotEqual(result["*A"]["route-uid"], result["*B"]["route-uid"])
        self.assertEqual(result["*A"]["comment"], "Nord USA")
        self.assertEqual(result["*A"]["immediate-gw"], "nord-wg")
        self.assertTrue(result["*C"]["dynamic"])
        self.assertIn("usa", result["*A"]["route-name"])
        self.assertIn("nord-wg", result["*A"]["route-name"])

    def test_disabled_and_unknown_flags_never_claim_active(self):
        rows = [
            {
                ".id": "*1",
                "dst-address": "1.1.1.0/24",
                "active": "yes",
                "disabled": "yes",
                "dynamic": "no",
            },
            {
                ".id": "*2",
                "dst-address": "2.2.2.0/24",
                "active": "no",
                "disabled": "no",
                "dynamic": "no",
            },
            {
                ".id": "*3",
                "dst-address": "3.3.3.0/24",
                "disabled": False,
                "dynamic": False,
            },
            {
                ".id": "*4",
                "dst-address": "4.4.4.0/24",
                "active": "maybe",
                "dynamic": False,
            },
            {"dst-address": "5.5.5.0/24", "active": True},
        ]
        result = load_routes().parse_routes(rows)
        self.assertEqual(set(result), {"*1", "*2", "*3"})
        self.assertFalse(result["*1"]["active"])
        self.assertFalse(result["*2"]["active"])
        self.assertFalse(result["*3"]["active"])

    def test_unavailable_snapshot_is_not_stale_route_data(self):
        self.assertIsNone(load_routes().parse_routes(None))
        self.assertEqual(load_routes().parse_routes([]), {})

    def test_coordinator_queries_read_only_and_clears_denied_snapshot(self):
        tree = ast.parse((PACKAGE / "coordinator.py").read_text())
        klass = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "MikrotikCoordinator"
        )
        method = next(
            n
            for n in klass.body
            if isinstance(n, ast.FunctionDef) and n.name == "get_routes"
        )
        method.decorator_list = []
        namespace = {"parse_routes": load_routes().parse_routes}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                "coordinator.py",
                "exec",
            ),
            namespace,
        )
        calls = []

        class API:
            def query(self, *args, **kwargs):
                calls.append((args, kwargs))
                return None

        class Coordinator:
            api = API()
            ds = {"route": {"*1": {"active": True}}}
            get_routes = namespace["get_routes"]

        coordinator = Coordinator()
        coordinator.get_routes()
        self.assertIsNone(coordinator.ds["route"])
        self.assertEqual(
            calls,
            [
                (
                    ("/ip/route",),
                    {
                        "command": "print",
                        "args": {
                            ".proplist": ".id,dst-address,routing-table,gateway,immediate-gw,comment,active,disabled,dynamic"
                        },
                        "ignore_trap": True,
                    },
                )
            ],
        )

    def test_route_entity_is_read_only_and_uses_routeros_identity(self):
        tree = ast.parse((PACKAGE / "binary_sensor_types.py").read_text())
        sensor_types = next(
            n
            for n in tree.body
            if isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "SENSOR_TYPES"
        )
        value = sensor_types.value
        assert isinstance(value, ast.Tuple)
        routes = [
            n
            for n in value.elts
            if isinstance(n, ast.Call)
            and any(
                k.arg == "key"
                and isinstance(k.value, ast.Constant)
                and k.value.value == "route_active"
                for k in n.keywords
            )
        ]
        self.assertEqual(len(routes), 1)
        values = {
            k.arg: ast.literal_eval(k.value)
            for k in routes[0].keywords
            if isinstance(k.value, (ast.Constant, ast.List))
        }
        self.assertEqual(values["data_path"], "route")
        self.assertEqual(values["data_name"], "route-name")
        self.assertEqual(values["data_attribute"], "active")
        self.assertEqual(values["data_reference"], "route-uid")
        self.assertEqual(values["ha_group"], "System")
        self.assertEqual(values["func"], "MikrotikBinarySensor")
        self.assertIn("routing-table", (PACKAGE / "binary_sensor_types.py").read_text())
        self.assertIn("immediate-gw", (PACKAGE / "binary_sensor_types.py").read_text())

    def test_route_option_is_opt_in_in_setup_and_polling(self):
        const = (PACKAGE / "const.py").read_text()
        flow = (PACKAGE / "config_flow.py").read_text()
        coordinator = (PACKAGE / "coordinator.py").read_text()
        strings = (PACKAGE / "strings.json").read_text()
        self.assertIn("DEFAULT_SENSOR_ROUTES = False", const)
        self.assertIn(
            "vol.Optional(\n                        CONF_SENSOR_ROUTES,", flow
        )
        self.assertIn("self.option_sensor_routes", coordinator)
        self.assertIn("self.major_fw_version >= 7", coordinator)
        self.assertIn('"sensor_routes"', strings)

    def test_resolved_gateway_is_redacted_from_diagnostics(self):
        tree = ast.parse((PACKAGE / "const.py").read_text())
        redaction = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TO_REDACT" for t in n.targets)
        )
        self.assertIn("immediate-gw", ast.literal_eval(redaction.value))


if __name__ == "__main__":
    unittest.main()
