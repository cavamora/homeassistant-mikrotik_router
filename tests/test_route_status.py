"""Read-only route status tests; runnable without Home Assistant installed."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
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


def load_entity_method(name):
    """Exercise the real entity method without importing the HA runtime."""
    tree = ast.parse((PACKAGE / "entity.py").read_text())
    klass = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "MikrotikEntity"
    )
    method = next(
        n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    method.decorator_list = []
    namespace = {
        "DeviceInfo": dict,
        "DOMAIN": "mikrotik_router",
        "CONF_SSL": "ssl",
        "CONF_HOST": "host",
        "device_registry": SimpleNamespace(
            async_get_device_id_by_identifier=lambda *args, **kwargs: "router-device"
        ),
        "slugify": lambda value: value.replace("*", "").replace(" ", "_"),
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
            "entity.py",
            "exec",
        ),
        namespace,
    )
    return namespace[name]


def load_skip_sensor():
    tree = ast.parse((PACKAGE / "entity.py").read_text())
    method = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_skip_sensor"
    )
    namespace = {
        "CONF_SENSOR_PORT_TRAFFIC": "port_traffic",
        "DEFAULT_SENSOR_PORT_TRAFFIC": False,
        "CONF_SENSOR_PORT_TRACKER": "port_tracker",
        "DEFAULT_SENSOR_PORT_TRACKER": False,
        "CONF_SENSOR_NETWATCH_TRACKER": "netwatch_tracker",
        "DEFAULT_SENSOR_NETWATCH_TRACKER": False,
        "CONF_TRACK_HOSTS": "track_hosts",
        "DEFAULT_TRACK_HOSTS": False,
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
            "entity.py",
            "exec",
        ),
        namespace,
    )
    return namespace["_skip_sensor"]


class RouteStatusTest(unittest.TestCase):
    def test_disabled_static_route_without_active_value_is_off_with_attributes(self):
        # /ip/route may omit a requested status property with no value.
        row = {
            ".id": "*80000024",
            "dst-address": "0.0.0.0/0",
            "routing-table": "main",
            "gateway": "192.0.2.1",
            "comment": "FORCAR PELA CLARO (QUEM TA NA LISTA)",
            "disabled": "true",
            "dynamic": "false",
        }
        route = load_routes().parse_routes([row])[row[".id"]]
        self.assertIs(route["active"], False)
        self.assertIs(route["disabled"], True)
        self.assertIs(route["inactive"], False)
        self.assertEqual(route["comment"], row["comment"])
        self.assertEqual(route["gateway"], row["gateway"])
        self.assertEqual(route["route-uid"], row[".id"].encode().hex())

    def test_explicit_inactive_route_without_disabled_value_is_off(self):
        route = load_routes().parse_routes(
            [{".id": "*I", "active": "false", "comment": "USHI"}]
        )["*I"]
        self.assertIs(route["active"], False)
        self.assertIs(route["inactive"], True)
        self.assertIs(route["disabled"], False)

    def test_routeros_ushi_route_without_active_flag_is_off_with_details(self):
        row = {
            ".id": "*80000024",
            "dst-address": "0.0.0.0/0",
            "routing-table": "claro",
            "gateway": "8.8.4.4",
            "comment": "FORCAR PELA CLARO (QUEM TA NA LISTA)",
            "disabled": "false",
            "inactive": "true",
        }
        route = load_routes().parse_routes([row])[row[".id"]]
        self.assertIs(route["active"], False)
        self.assertIs(route["inactive"], True)
        self.assertEqual(route["comment"], row["comment"])
        self.assertEqual(route["dst-address"], row["dst-address"])
        self.assertEqual(route["gateway"], row["gateway"])

    def test_missing_boolean_flags_on_existing_route_do_not_hide_it(self):
        route = load_routes().parse_routes(
            [{".id": "*I", "comment": "Backup ISP", "dst-address": "0.0.0.0/0"}]
        )["*I"]
        self.assertIs(route["active"], False)
        self.assertIs(route["inactive"], True)
        self.assertEqual(route["comment"], "Backup ISP")

    def test_invalid_flag_value_is_not_silently_treated_as_false(self):
        route = load_routes().parse_routes(
            [{".id": "*I", "comment": "Ambiguous", "active": "maybe"}]
        )["*I"]
        self.assertIsNone(route["active"])

    def test_only_commented_routes_without_dynamic_flag_are_discovered(self):
        rows = [
            {".id": "*A", "comment": "Backup ISP", "dynamic": False, "active": False},
            {".id": "*B", "comment": "Disabled ISP", "dynamic": "no", "disabled": True},
            {".id": "*C", "comment": "Dynamic ISP", "dynamic": True},
            {".id": "*D", "comment": "", "dynamic": False},
            {".id": "*E", "comment": "   ", "dynamic": False},
            {".id": "*F", "comment": "Unknown flag", "active": False},
        ]
        data = load_routes().parse_routes(rows)
        description = SimpleNamespace(data_path="route", func="MikrotikBinarySensor")
        skip = load_skip_sensor()
        accepted = {
            uid
            for uid in data
            if not skip(SimpleNamespace(options={}), description, data, uid)
        }
        self.assertEqual(accepted, {"*A", "*B", "*F"})

    def test_stale_ip_route_cleanup_is_narrow_and_preserves_named_routes(self):
        entry = "mikrotik-entry"

        def item(entity_id, original_name, uid, **overrides):
            values = {
                "entity_id": entity_id,
                "original_name": original_name,
                "unique_id": f"mikrotik-route_active-{uid}",
                "name": None,
                "platform": "mikrotik_router",
                "config_entry_id": entry,
            }
            values.update(overrides)
            return SimpleNamespace(**values)

        ip_name = "10.0.0.0/24 [main] via WAN - 1 - Vivo Route active"
        old_ip = item("binary_sensor.mikrotik_routes_10_0_0_0", ip_name, "old")
        restored_comment = item("binary_sensor.restored_comment", ip_name, "kept")
        custom = item("binary_sensor.user_named", ip_name, "custom", name="My route")
        other_entry = item(
            "binary_sensor.other_router", ip_name, "other", config_entry_id="other"
        )
        other_platform = item(
            "binary_sensor.other_platform", ip_name, "other", platform="other"
        )
        active_named = item("binary_sensor.claro", "FORCAR PELA CLARO", "claro")
        result = load_routes().stale_ip_route_entity_ids(
            [
                old_ip,
                restored_comment,
                custom,
                other_entry,
                other_platform,
                active_named,
            ],
            entry,
            {"kept", "claro"},
        )
        self.assertEqual(result, [old_ip.entity_id])

    def test_stale_ip_route_cleanup_does_not_remove_foreign_entity_domains(self):
        old_switch = SimpleNamespace(
            entity_id="switch.mikrotik_routes_ip",
            original_name="10.0.0.0/24 [main] via bridge Route active",
            unique_id="mikrotik-route_active-old",
            name=None,
            platform="mikrotik_router",
            config_entry_id="entry",
        )
        self.assertEqual(
            load_routes().stale_ip_route_entity_ids([old_switch], "entry", set()), []
        )

    def test_setup_purges_only_stale_ip_route_entities_with_valid_snapshot(self):
        source = ast.parse((PACKAGE / "binary_sensor.py").read_text())
        cleanup = next(
            node
            for node in source.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_cleanup_stale_ip_route_entities"
        )
        setup = next(
            node
            for node in source.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_setup_entry"
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == cleanup.name
                for node in ast.walk(setup)
            )
        )
        removed = []
        old = SimpleNamespace(
            entity_id="binary_sensor.mikrotik_routes_10_0_0_0",
            original_name="10.0.0.0/24 [main] via WAN - 1 - Vivo Route active",
            unique_id="mikrotik-route_active-old",
            name=None,
            platform="mikrotik_router",
            config_entry_id="entry",
        )
        registry = SimpleNamespace(
            entities={old.entity_id: old}, async_remove=removed.append
        )
        scope = {
            "entity_registry": SimpleNamespace(async_get=lambda hass: registry),
            "stale_ip_route_entity_ids": load_routes().stale_ip_route_entity_ids,
            "CONF_SENSOR_ROUTES": "sensor_routes",
            "DEFAULT_SENSOR_ROUTES": False,
            "_LOGGER": SimpleNamespace(info=lambda *args: None),
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[cleanup], type_ignores=[])),
                "binary_sensor.py",
                "exec",
            ),
            scope,
        )
        entry = SimpleNamespace(
            entry_id="entry",
            options={"sensor_routes": True},
            runtime_data=SimpleNamespace(
                data_coordinator=SimpleNamespace(data={"route": None})
            ),
        )
        scope[cleanup.name](object(), entry)
        self.assertEqual(removed, [])
        entry.runtime_data.data_coordinator.data["route"] = {
            "*A": {
                "route-uid": "kept",
                "comment": "FORCAR PELA CLARO",
                "dynamic": False,
            }
        }
        scope[cleanup.name](object(), entry)
        self.assertEqual(removed, [old.entity_id])

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
        # A route with an ID stays visible even when optional flags are absent.
        self.assertEqual(set(result), {"*1", "*2", "*3", "*4"})
        self.assertFalse(result["*1"]["active"])
        self.assertFalse(result["*2"]["active"])
        # Unset A/X RouterOS flags identify an existing inactive route.
        self.assertIs(result["*3"]["active"], False)
        self.assertIsNone(result["*4"]["active"])

    def test_all_commented_non_dynamic_routes_survive_missing_flags(self):
        rows = [
            {".id": "*A", "comment": "VPN fallback", "dynamic": False, "active": False},
            {
                ".id": "*B",
                "comment": "Disabled path",
                "disabled": True,
                "dynamic": False,
            },
            {".id": "*C", "comment": "Static unknown", "active": False},
            {".id": "*D", "comment": "Dynamic path", "dynamic": True},
        ]
        routes = load_routes().parse_routes(rows)
        self.assertEqual(set(routes), {"*A", "*B", "*C", "*D"})
        self.assertFalse(routes["*A"]["active"])
        self.assertIs(routes["*B"]["active"], False)
        self.assertIsNone(routes["*C"]["dynamic"])
        self.assertEqual(routes["*C"]["comment"], "Static unknown")

    def test_unavailable_snapshot_is_not_stale_route_data(self):
        self.assertIsNone(load_routes().parse_routes(None))
        self.assertEqual(load_routes().parse_routes([]), {})

    def test_route_flags_are_tristate_and_inactive_comes_from_ip_status(self):
        ip = [
            {
                ".id": "*1",
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "active": False,
                "disabled": False,
                "dynamic": False,
            },
            {
                ".id": "*2",
                "dst-address": "10.0.0.0/8",
                "active": True,
                "disabled": False,
                "dynamic": True,
            },
            {
                ".id": "*3",
                "dst-address": "192.0.2.0/24",
                "active": False,
                "disabled": True,
                "dynamic": False,
            },
        ]
        routing = [
            {
                ".id": "*DIFFERENT",
                "afi": "ip4",
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "unreachable": "yes",
                "static": "yes",
                "hw-offloaded": "no",
            },
            {
                "afi": "ip6",
                "dst-address": "10.0.0.0/8",
                "routing-table": "main",
                "gateway": "",
                "unreachable": True,
            },
        ]
        routes = load_routes().parse_routes(ip, routing)
        self.assertEqual(routes["*1"]["route-uid"], "*1".encode().hex())
        self.assertEqual(routes["*1"]["inactive"], True)
        self.assertEqual(routes["*1"]["unreachable"], True)
        self.assertEqual(routes["*1"]["static"], True)
        self.assertEqual(routes["*1"]["hw-offloaded"], False)
        self.assertEqual(routes["*2"]["inactive"], False)
        self.assertIsNone(routes["*2"]["unreachable"])
        self.assertIsNone(routes["*2"]["static"])
        self.assertIsNone(routes["*2"]["hw-offloaded"])
        self.assertEqual(routes["*3"]["inactive"], False)

    def test_duplicate_routing_candidates_do_not_assign_flags(self):
        ip = [
            {
                ".id": "*1",
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "active": False,
                "disabled": False,
                "dynamic": False,
            }
        ]
        candidates = [
            {
                "afi": "ip4",
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "unreachable": True,
                "static": True,
                "hw-offloaded": True,
            },
            {
                "afi": "ip4",
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "unreachable": False,
                "static": False,
                "hw-offloaded": False,
            },
        ]
        route = load_routes().parse_routes(ip, candidates)["*1"]
        for flag in ("unreachable", "static", "hw-offloaded"):
            self.assertIsNone(route[flag])

    def test_duplicate_ip_candidates_do_not_share_routing_flags(self):
        ip = [
            {
                ".id": ident,
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "active": False,
                "disabled": False,
                "dynamic": False,
            }
            for ident in ("*1", "*2")
        ]
        routing = [
            {
                "afi": "ip4",
                "dst-address": "0.0.0.0/0",
                "routing-table": "vpn",
                "gateway": "wg",
                "unreachable": True,
            }
        ]
        for route in load_routes().parse_routes(ip, routing).values():
            self.assertIsNone(route["unreachable"])

    def test_missing_or_invalid_routing_flags_remain_unknown(self):
        ip = [
            {
                ".id": "*1",
                "dst-address": "0.0.0.0/0",
                "active": True,
                "disabled": False,
                "dynamic": False,
            }
        ]
        routing = [
            {
                "afi": "ip4",
                "dst-address": "0.0.0.0/0",
                "routing-table": "main",
                "gateway": "",
                "unreachable": "maybe",
            }
        ]
        route = load_routes().parse_routes(ip, routing)["*1"]
        self.assertIsNone(route["unreachable"])
        self.assertIsNone(route["static"])
        self.assertIsNone(route["hw-offloaded"])
        self.assertIsNone(load_routes().parse_routes(ip, None)["*1"]["unreachable"])

    def test_incomplete_cross_menu_identity_does_not_match(self):
        ip = [
            {
                ".id": "*1",
                "dst-address": "0.0.0.0/0",
                "active": False,
                "disabled": False,
                "dynamic": False,
            }
        ]
        routing = [
            {
                "afi": "ip4",
                "dst-address": "0.0.0.0/0",
                "routing-table": "main",
                "gateway": "",
                "unreachable": True,
            }
        ]
        self.assertIsNone(load_routes().parse_routes(ip, routing)["*1"]["unreachable"])

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
                            ".proplist": ".id,dst-address,routing-table,gateway,immediate-gw,comment,active,inactive,disabled,dynamic"
                        },
                        "ignore_trap": True,
                    },
                ),
                (
                    ("/routing/route",),
                    {
                        "command": "print",
                        "args": {
                            ".proplist": "afi,dst-address,routing-table,gateway,unreachable,static,hw-offloaded"
                        },
                        "ignore_trap": True,
                    },
                ),
            ],
        )

    def test_coordinator_preserves_ip_routes_when_optional_routing_query_fails(self):
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

        class API:
            def query(self, path, **kwargs):
                if path == "/routing/route":
                    return None
                return [
                    {
                        ".id": "*1",
                        "dst-address": "0.0.0.0/0",
                        "active": False,
                        "disabled": False,
                        "dynamic": False,
                    }
                ]

        class Coordinator:
            api = API()
            ds = {"route": None}
            get_routes = namespace["get_routes"]

        coordinator = Coordinator()
        coordinator.get_routes()
        self.assertEqual(coordinator.ds["route"]["*1"]["inactive"], True)
        self.assertIsNone(coordinator.ds["route"]["*1"]["unreachable"])

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
        self.assertEqual(values["ha_group"], "Routes")
        self.assertTrue(values["data_name_comment"])
        self.assertEqual(values["func"], "MikrotikBinarySensor")
        self.assertIn("routing-table", (PACKAGE / "binary_sensor_types.py").read_text())
        self.assertIn("immediate-gw", (PACKAGE / "binary_sensor_types.py").read_text())

    def test_route_device_is_router_scoped_and_shared_by_all_its_routes(self):
        device_info = load_entity_method("device_info")

        def entity(serial, name, route_id):
            return SimpleNamespace(
                hass=object(),
                _inst=name,
                _config_entry=SimpleNamespace(entry_id=f"entry-{serial}"),
                _data=load_routes().parse_routes(
                    [
                        {
                            ".id": route_id,
                            "active": True,
                            "disabled": False,
                            "dynamic": False,
                        }
                    ]
                )[route_id],
                entity_description=SimpleNamespace(
                    ha_group="Routes",
                    ha_connection=None,
                    ha_connection_value=None,
                    data_reference="route-uid",
                    data_name="route-name",
                    key="route_active",
                ),
                coordinator=SimpleNamespace(
                    data={
                        "routerboard": {"serial-number": serial},
                        "resource": {"board-name": "hEX", "platform": "MikroTik"},
                    },
                    config_entry=SimpleNamespace(
                        data={"ssl": False, "host": "router.local"}
                    ),
                ),
            )

        first = device_info(entity("SERIAL-A", "Home", "*1"))
        second = device_info(entity("SERIAL-A", "Home", "*2"))
        other = device_info(entity("SERIAL-B", "Office", "*1"))
        self.assertEqual(first["identifiers"], second["identifiers"])
        self.assertNotEqual(first["identifiers"], other["identifiers"])
        self.assertEqual(first["name"], "Home Routes")
        # Preserve the old device identifier so 2.3.5 installations do not
        # create a second device when only its displayed name changes.
        self.assertEqual(first["identifiers"], {("mikrotik_router", "SERIAL-A-Rotas")})
        self.assertEqual(first["via_device_id"], "router-device")
        self.assertNotIn("connections", first)

    def test_route_comment_name_and_fallback_preserve_existing_unique_id(self):
        custom_name = load_entity_method("custom_name")
        unique_id = load_entity_method("_mikrotik_unique_id")
        description = SimpleNamespace(
            data_name_comment=True,
            data_name="route-name",
            data_reference="route-uid",
            name="Route active",
            key="route_active",
        )
        for comment, expected in (
            ("Nord USA", "Nord USA"),
            ("", "0.0.0.0/0 [vpn] via wg Route active"),
        ):
            row = load_routes().parse_routes(
                [
                    {
                        ".id": "*A",
                        "comment": comment,
                        "dst-address": "0.0.0.0/0",
                        "routing-table": "vpn",
                        "gateway": "wg",
                        "active": False,
                        "disabled": False,
                        "dynamic": False,
                    }
                ]
            )["*A"]
            entity = SimpleNamespace(
                _uid="*A", _inst="Home", _data=row, entity_description=description
            )
            self.assertEqual(custom_name(entity), expected)
            self.assertEqual(unique_id(entity), "home-route_active-2a41")

    def test_route_with_unknown_active_flag_is_unavailable_not_off(self):
        tree = ast.parse((PACKAGE / "entity.py").read_text())
        klass = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "MikrotikEntity"
        )
        method = next(
            n
            for n in klass.body
            if isinstance(n, ast.FunctionDef) and n.name == "available"
        )
        klass.body = [method]
        klass.bases = [ast.Name(id="Base", ctx=ast.Load())]
        method.decorator_list = [ast.Name(id="property", ctx=ast.Load())]

        class Base:
            available = True

        scope = {"Base": Base}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[klass], type_ignores=[])),
                "entity.py",
                "exec",
            ),
            scope,
        )
        entity = scope["MikrotikEntity"]()
        setattr(
            entity,
            "entity_description",
            SimpleNamespace(data_path="route", data_attribute="active"),
        )
        setattr(entity, "_uid", "*A")
        setattr(
            entity,
            "coordinator",
            SimpleNamespace(
                data={"route": {"*A": {"active": None}, "*B": {"active": False}}}
            ),
        )
        self.assertFalse(entity.available)
        setattr(entity, "_uid", "*B")
        self.assertTrue(entity.available)

    def test_existing_route_sensor_exposes_all_flags_as_attributes(self):
        tree = ast.parse((PACKAGE / "binary_sensor_types.py").read_text())
        attrs = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "DEVICE_ATTRIBUTES_ROUTE"
                for t in n.targets
            )
        )
        exposed = ast.literal_eval(attrs.value)
        for flag in (
            "active",
            "inactive",
            "unreachable",
            "static",
            "hw-offloaded",
            "disabled",
            "dynamic",
        ):
            self.assertIn(flag, exposed)

    def test_manifest_and_docs_describe_route_flags(self):
        manifest = json.loads((PACKAGE / "manifest.json").read_text())
        self.assertEqual(manifest["version"], "2.3.7")
        readme = (ROOT / "README.md").read_text()
        self.assertIn("U (unreachable)", readme)
        self.assertIn("ambiguous", readme)
        self.assertIn("<router name> Routes", readme)
        self.assertIn("commented routes", readme)
        self.assertIn("old IP-labelled", readme)
        self.assertIn("entity_id", readme)

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
