from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    OPENAI_API_KEY: str = ""
    WEBHOOK_BASE: str
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_SECRET: str
    DB_PATH: str = "/var/data/moslav.sqlite3"
    ADMIN_IDS: list[int] = []

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if v is None or v == "":
            return []

        if isinstance(v, list):
            return [int(x) for x in v]

        if isinstance(v, str):
            s = v.strip()
            if s.startswith("[") and s.endswith("]"):
                import json
                return [int(x) for x in json.loads(s)]
            return [int(x.strip()) for x in s.split(",") if x.strip()]

        return v

    @computed_field
    @property
    def admin_id_set(self) -> set[int]:
        return set(self.ADMIN_IDS)

    @property
    def webhook_url(self) -> str:
        return f"{self.WEBHOOK_BASE.rstrip('/')}{self.WEBHOOK_PATH}"


settings = Settings()

