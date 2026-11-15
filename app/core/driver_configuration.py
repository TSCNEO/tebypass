"""Router driver prerequisites and the stable identity of an applied policy.

Settings changes need two different questions answered:

* Is this driver candidate complete enough to apply a policy at all?
* Does the *current* configuration identify the same remote policy location?

Both answers are configuration-only. Drivers remain responsible for the actual remote
state; this module never performs network or database I/O.
"""

import hashlib
import json

from app.core.config import Settings

# Keys that describe *where* and *how* a driver applies its policy. Changing any of them
# means the new configuration cannot own the policy applied by the previous one.
IDENTITY_KEYS: tuple[str, ...] = (
    "router_driver",
    "unifi_controller_url",
    "unifi_site",
    "unifi_route_name",
    "omada_base_url",
    "omada_controller_id",
    "omada_site",
    "omada_route_name",
    "opnsense_host",
    "opnsense_route_name",
    "mikrotik_api_url",
    "mikrotik_address_list",
    "opnsense_alias_name",
)

# Minimum values needed before a driver can open a connection and mutate remote state.
# Values that always have a usable default (site, route name, alias) are not listed.
_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "unifi": ("unifi_controller_url", "unifi_api_key"),
    "mikrotik": (
        "mikrotik_api_url",
        "mikrotik_username",
        "mikrotik_password",
        "mikrotik_routing_table",
    ),
    "omada": (
        "omada_base_url",
        "omada_client_id",
        "omada_client_secret",
        "omada_controller_id",
        "omada_target",
    ),
    "opnsense": ("opnsense_host", "opnsense_api_key", "opnsense_api_secret"),
}


def driver_identity(cfg: Settings) -> str:
    """Return a stable digest of the configuration that owns a remote policy."""
    payload = {key: getattr(cfg, key, None) for key in IDENTITY_KEYS}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def missing_driver_configuration(cfg: Settings) -> tuple[str, ...]:
    """Return the settings still missing before the selected driver can write.

    An empty tuple means the configuration is complete enough to attempt a remote
    mutation. The virtual gateway has no static credentials; whether its sidecar socket
    exists is a runtime condition, not a setting, and is reported by the driver itself.
    """
    missing: list[str] = []
    for key in _REQUIRED_KEYS.get(cfg.router_driver, ()):
        value = getattr(cfg, key, None)
        if value is None or not str(value).strip():
            missing.append(key)
    return tuple(missing)
