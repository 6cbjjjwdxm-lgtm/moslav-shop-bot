import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BOT_TOKEN: str

    # Может быть пустым — тогда будем использовать RENDER_EXTERNAL_URL (на Render) или ничего (локально)
    WEBHOOK_BASE_URL: str | None = None
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_SECRET: str

    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o-mini"

    ADMIN_IDS: str = ""
    DB_PATH: str = "./data.db"

    @property
    def effective_base_url(self) -> str:
        # Если в .env задан WEBHOOK_BASE_URL — берём его.
        # Если нет, пробуем RENDER_EXTERNAL_URL (Render подставляет свой https://... адрес в эту переменную).
        env_url = os.getenv("RENDER_EXTERNAL_URL", "")
        base = (self.WEBHOOK_BASE_URL or env_url or "").rstrip("/")
        return base

    @property
    def webhook_url(self) -> str:
        base = self.effective_base_url
        if not base:
            return ""
        path = self.WEBHOOK_PATH if self.WEBHOOK_PATH.startswith("/") else f"/{self.WEBHOOK_PATH}"
        return f"{base}{path}"

    @property
    def admin_id_set(self) -> set[int]:
        raw = [x.strip() for x in self.ADMIN_IDS.split(",") if x.strip()]
        out: set[int] = set()
        for x in raw:
            try:
                out.add(int(x))
            except ValueError:
                pass
        return out


settings = Settings()
