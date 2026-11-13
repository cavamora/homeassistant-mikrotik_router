"""Normalize read-only RouterOS IPv4 route snapshots for HA."""


def _flag(value):
    """Accept RouterOS API boolean values without treating 'false' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return {"true": True, "yes": True, "false": False, "no": False}.get(
            value.lower()
        )
    return None


def parse_routes(rows):
    """Return IPv4 routes keyed by RouterOS ID; None means query unavailable."""
    if rows is None:
        return None

    routes = {}
    for row in rows:
        route_id = row.get(".id")
        active = _flag(row.get("active", False))
        disabled = _flag(row.get("disabled", False))
        dynamic = _flag(row.get("dynamic", False))
        if not route_id or active is None or disabled is None or dynamic is None:
            continue
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
            "active": active and not disabled,
            "disabled": disabled,
            "dynamic": dynamic,
        }
    return routes
