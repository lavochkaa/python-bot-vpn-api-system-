from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str = ""
    vpn_channel_id: int | None = None
    vpn_channel_username: str = "@vpn_channel"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vpnbot"
    log_level: str = "INFO"
    payment_provider: str = "stub"
    vpn_provider: str = "stub"
    payment_provider_token: str = "TEST:TOKEN"
    payment_currency: str = "RUB"
    admin_ids: str = ""
    message_banner_path: str = "5554EDCD-AA4B-4150-A75C-7A75E0155E5A.PNG"
    message_banner_file_id: str = ""

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
