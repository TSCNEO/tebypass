"""Application configuration module."""

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
    router_driver: str = "mock"  # mock, virtual_gateway, unifi, mikrotik
    sync_interval_seconds: int = 300
    sliding_window_seconds: int = 3600
    master_pin_hash: str | None = None
    secret_key: str = "change-me-in-production-neutral-secret-key"
    log_level: str = "INFO"

    # Gateway Driver specific configuration
    gateway_nft_set_name: str = "tebypass_targets"
    gateway_control_socket: str | None = None

    # UniFi Driver specific configuration
    unifi_controller_url: str | None = None
    unifi_username: str | None = None
    unifi_password: str | None = None
    unifi_site: str = "default"
    unifi_group_name: str = "TeByPass_Targets"

    # MikroTik Driver specific configuration
    mikrotik_api_url: str | None = None
    mikrotik_username: str | None = None
    mikrotik_password: str | None = None
    mikrotik_address_list: str = "TeByPass_Targets"


settings = Settings()
