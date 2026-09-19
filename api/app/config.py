from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """从 api/.env 读取配置。这里的值全部是服务端机密，绝不下发到前端。"""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # 逗号分隔。手机真机调试时把局域网地址加进来，例如 http://192.168.1.5:5173
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
