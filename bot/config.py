from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str = ""
    vpn_channel_id: int | None = None
    vpn_channel_username: str = "@vpn_channel"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vpnbot"
    log_level: str = "INFO"
    payment_provider: str = "stub"
    vpn_provider: str = "api"
    vpn_api_base_url: str = ""
    vpn_api_key: str = ""
    vpn_api_timeout_seconds: int = 20
    vpn_api_verify_ssl: bool = True
    sub_combiner_base_url: str = ""
    sub_domain: str = ""
    hiddify_admin_link: str = ""
    hiddify_api_key: str = ""
    hiddify_verify_ssl: bool = False
    hiddify_profile_name: str = "CRYSTAL VPN"
    hiddify_subscription_url_template: str = ""
    hiddify_default_traffic_gb: int = 50
    hiddify_default_duration_days: int = 30
    payment_provider_token: str = "TEST:TOKEN"
    payment_currency: str = "RUB"
    admin_ids: str = ""
    message_banner_path: str = "5554EDCD-AA4B-4150-A75C-7A75E0155E5A.PNG"
    message_banner_file_id: str = ""
    connect_guide_url: str = "https://teletype.in/@crystal-vpn/connect"
    # TODO(DEEPLINK): replace templates with production-ready client schemes if needed.
    happ_deeplink_template: str = "happ://import?url={key_url}"
    streisand_deeplink_template: str = "streisand://import-subscription?url={key_url}"
    hiddify_deeplink_template: str = "hiddify://install-config?url={key_url}"
    v2_deeplink_template: str = "v2rayng://install-config?url={key_b64}"

    @property
    def normalized_channel_username(self) -> str:
        value = self.vpn_channel_username.strip()
        value = value.replace("https://t.me/", "").replace("http://t.me/", "")
        value = value.strip("/")
        if not value.startswith("@"):
            value = f"@{value}"
        return value

    @property
    def admin_id_set(self) -> set[int]:
        return {
            int(chunk.strip())
            for chunk in self.admin_ids.split(",")
            if chunk.strip().isdigit()
        }


settings = Settings()
