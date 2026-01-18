import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    BOT_TOKEN: str

    WEBHOOK_BASE_URL: str | None = None
    WEBHOOK_PATH: str = '/webhook'
    WEBHOOK_SECRET: str

    OPENAI_API_KEY: str
    OPENAI_MODEL: str = 'gpt-4o-mini'

    ADMIN_IDS: str = ''
    DB_PATH: str = './data.db'

    @property
    def effective_base_url(self) -> str:
        # Приоритет:
        # 1) WEBHOOK_BASE_URL из переменных/ .env
        # 2) Render: RENDER_EXTERNAL_URL
        # 3) Railway: RAILWAY_PUBLIC_DOMAIN (без https://)
        render_url = os.getenv('RENDER_EXTERNAL_URL', '').strip()
        railway_domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()

        if self.WEBHOOK_BASE_URL:
            base = self.WEBHOOK_BASE_URL.strip()
        elif render_url:
            base = render_url
        elif railway_domain:
            base = f'https://{railway_domain}'
        else:
            base = ''

        return base.rstrip('/')

    @property
    def webhook_url(self) -> str:
        base = self.effective_base_url
        if not base:
            return ''
        path = self.WEBHOOK_PATH if self.WEBHOOK_PATH.startswith('/') else f'/{self.WEBHOOK_PATH}'
        return f'{base}{path}'

    @property
    def admin_id_set(self) -> set[int]:
        raw = [x.strip() for x in self.ADMIN_IDS.split(',') if x.strip()]
        out: set[int] = set()
        for x in raw:
            try:
                out.add(int(x))
            except ValueError:
                pass
        return out


settings = Settings()
