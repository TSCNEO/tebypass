"""Application configuration module."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """TeByPass typed settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_prefix="TEBYPASS_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 9384
    db_path: str = "data/tebypass.db"
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TEBYPASS_DATABASE_URL", "DATABASE_URL"),
    )
    router_driver: str = Field(
        default="mock",
        validation_alias=AliasChoices(
            "TEBYPASS_ROUTER_DRIVER",
            "TEBYPASS_ROUTER_TYPE",
            "ROUTER_DRIVER",
            "ROUTER_TYPE",
        ),
    )  # mock, virtual_gateway, unifi, mikrotik
    sync_interval_seconds: int = 300
    sliding_window_seconds: int = 3600
    resolve_ipv6: bool = Field(
        default=False,
        validation_alias=AliasChoices("TEBYPASS_RESOLVE_IPV6", "RESOLVE_IPV6"),
    )
    master_pin_hash: str | None = None
    secret_key: str = "change-me-in-production-neutral-secret-key"
    log_level: str = "INFO"

    # Feed ingestion behaviour (persisted in SQLite and editable from the UI)
    feed_force_active: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "TEBYPASS_FEED_FORCE_ACTIVE",
            "FEED_FORCE_ACTIVE",
        ),
    )
    feed_isp_filter: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TEBYPASS_FEED_ISP_FILTER",
            "FEED_ISP_FILTER",
        ),
    )
    hayahora_feed_url: str = Field(
        default="https://hayahora.futbol/estado/data.json",
        validation_alias=AliasChoices(
            "TEBYPASS_HAYAHORA_FEED_URL",
            "HAYAHORA_FEED_URL",
        ),
    )

    # Gateway Driver specific configuration
    gateway_nft_set_name: str = "tebypass_targets"
    gateway_control_socket: str | None = None
    gateway_fail_closed: bool = True
    wireguard_config_path: str = "/data/wireguard/wg0.conf"

    # UniFi Driver specific configuration
    unifi_controller_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TEBYPASS_UNIFI_CONTROLLER_URL",
            "UNIFI_CONTROLLER_URL",
            "STOPLIGA_CONTROLLER_HOST",
        ),
    )
    unifi_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TEBYPASS_UNIFI_API_KEY", "UNIFI_API_KEY"),
    )
    unifi_username: str | None = None
    unifi_password: str | None = None
    unifi_site: str = Field(
        default="default",
        validation_alias=AliasChoices(
            "TEBYPASS_UNIFI_SITE",
            "UNIFI_SITE",
            "STOPLIGA_SITE",
        ),
    )
    unifi_group_name: str = Field(
        default="TeByPass_Targets",
        validation_alias=AliasChoices(
            "TEBYPASS_UNIFI_GROUP_NAME",
            "UNIFI_GROUP_NAME",
        ),
    )
    unifi_route_name: str = Field(
        default="TeByPass",
        validation_alias=AliasChoices(
            "TEBYPASS_UNIFI_ROUTE_NAME",
            "UNIFI_ROUTE_NAME",
            "STOPLIGA_ROUTE_NAME",
        ),
    )
    unifi_vpn_network_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "TEBYPASS_UNIFI_VPN_NETWORK_ID",
            "UNIFI_VPN_NETWORK_ID",
        ),
    )
    unifi_verify_tls: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "TEBYPASS_UNIFI_VERIFY_TLS",
            "UNIFI_VERIFY_TLS",
            "STOPLIGA_CONTROLLER_VERIFY_TLS",
        ),
    )

    # MikroTik Driver specific configuration
    mikrotik_api_url: str | None = None
    mikrotik_username: str | None = None
    mikrotik_password: str | None = None
    mikrotik_address_list: str = "TeByPass_Targets"


settings = Settings()
