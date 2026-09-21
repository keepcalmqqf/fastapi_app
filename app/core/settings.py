from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET_KEY = "dev-only-secret-key-please-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MODE: str = "DEV"

    # MySQL
    MYSQL_HOST: str = ""
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DATABASE: str = "fastapi_db"

    # Redis
    REDIS_HOST: str = ""
    REDIS_PORT: int = 6379

    # JWT
    SECRET_KEY: str = _DEV_SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # CORS（逗号分隔，如 "http://localhost,http://localhost:8080"）
    CORS_ORIGINS: str = "http://localhost,http://localhost:8080"

    # 可插拔能力开关
    ENABLE_RATE_LIMIT: bool = False
    RATE_LIMIT: str = "100/minute"
    ENABLE_METRICS: bool = False
    ENABLE_REQUEST_ID: bool = True

    @model_validator(mode="after")
    def _apply_mode_defaults(self) -> "Settings":
        if not self.MYSQL_HOST:
            self.MYSQL_HOST = "mysql" if self.MODE == "PROD" else "127.0.0.1"
        if not self.REDIS_HOST:
            self.REDIS_HOST = "redis" if self.MODE == "PROD" else "127.0.0.1"
        if self.MODE == "PROD":
            if self.SECRET_KEY == _DEV_SECRET_KEY:
                raise ValueError("PROD 模式必须通过环境变量设置 SECRET_KEY")
            if self.MYSQL_PASSWORD == "123456":
                raise ValueError("PROD 模式必须通过环境变量设置 MYSQL_PASSWORD")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def SQLALCHEMY_DATABASE_URL(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )


settings = Settings()
