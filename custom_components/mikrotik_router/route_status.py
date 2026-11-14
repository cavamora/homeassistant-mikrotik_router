"""Normalize read-only RouterOS IPv4 route snapshots for HA."""

from collections import Counter


def _flag(value):
    """Accept RouterOS API boolean values without treating 'false' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return {"true": True, "yes": True, "false": False, "no": False}.get(
            value.lower()
        )
    return None


def _route_key(row):
    """Only compare explicit route fields, never IDs from different menus."""
    if not all(field in row for field in ("dst-address", "routing-table", "gateway")):
        return None
    return (
        row["dst-address"],
        row["routing-table"],
        row["gateway"],
    )


def parse_routes(rows, routing_rows=None):
    """Return IPv4 routes keyed by RouterOS ID; None means query unavailable."""
    if rows is None:
        return None

    routes = {}
    ip_counts = Counter(_route_key(row) for row in rows if _route_key(row))
    routing_counts = Counter()
    routing_by_key = {}
    if routing_rows is not None:
        for row in routing_rows:
            if row.get("afi") != "ip4":
                continue
            key = _route_key(row)
            if key:
                routing_counts[key] += 1
                routing_by_key[key] = row

    for row in rows:
        route_id = row.get(".id")
        active = _flag(row.get("active"))
        disabled = _flag(row.get("disabled"))
        dynamic = _flag(row.get("dynamic"))
        if not route_id:
            continue
        key = _route_key(row)
        routing = (
            routing_by_key[key]
            if key and ip_counts[key] == 1 and routing_counts[key] == 1
            else {}
        )
        destination = row.get("dst-address", "")
        table = row.get("routing-table", "main")
        gateway = row.get("gateway", "")
        routes[route_id] = {
            ".id": route_id,
            # Hex preserves case/punctuation and distinguishes otherwise identical routes.
            "route-uid": str(route_id).encode("utf-8").hex(),
            "dst-address": destination,
            "routing-table": table,
            "gateway": gateway,
            "route-name": (
                f"{destination} [{table}] via {gateway}"
                if gateway
                else f"{destination} [{table}]"
            ),
            "immediate-gw": row.get("immediate-gw", ""),
            "comment": row.get("comment", ""),
            # Either observed false active or true disabled proves the route is off;
            # a missing flag alone does not prove its opposite.
            "active": (
                False
                if disabled is True or active is False
                else True
                if active is True and disabled is False
                else None
            ),
            "disabled": disabled,
            "dynamic": dynamic,
            # /ip/route's X/A/I status is exclusive; disabled is not inactive.
            "inactive": (
                False
                if disabled is True or active is True
                else True
                if active is False and disabled is False
                else None
            ),
            # Missing, invalid and ambiguous /routing/route observations are unknown.
            "unreachable": _flag(routing.get("unreachable")),
            "static": _flag(routing.get("static")),
            "hw-offloaded": _flag(routing.get("hw-offloaded")),
        }
    return routes
