from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET_KEY = "dev-only-secret-key-please-change-in-production"

# 允许的 JWT 签名算法（对称 HMAC 系列，防止误配 none 导致签名校验失效）
_ALLOWED_JWT_ALGORITHMS = ("HS256", "HS384", "HS512")

# HS256 的安全下限：密钥长度不应低于哈希输出长度（32 字节）
_MIN_SECRET_KEY_BYTES = 32


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
    REDIS_PASSWORD: str | None = None

    # JWT
    SECRET_KEY: str = _DEV_SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # 认证 Cookie（None 时 PROD 视为 Secure、DEV 不 Secure，见 cookie_secure 属性）
    COOKIE_SECURE: bool | None = None

    # /metrics 访问令牌（None 时公开访问，生产环境应配置以启用鉴权）
    METRICS_TOKEN: str | None = None

    # API 文档开关（None 时 DEV 开、PROD 关；显式设置则以设置为准）
    ENABLE_DOCS: bool | None = None

    # CORS（逗号分隔，如 "http://localhost,http://localhost:8080"）
    CORS_ORIGINS: str = "http://localhost,http://localhost:8080"

    # 前端构建产物目录（存在时由后端托管为 SPA）
    FRONTEND_DIST_DIR: str = "frontend/dist"

    # 可插拔能力开关
    ENABLE_RATE_LIMIT: bool = False
    RATE_LIMIT: str = "100/minute"
    ENABLE_METRICS: bool = False
    ENABLE_REQUEST_ID: bool = True
    ENABLE_MEMBER: bool = True

    @field_validator("JWT_ALGORITHM")
    @classmethod
    def _validate_jwt_algorithm(cls, v: str) -> str:
        if v not in _ALLOWED_JWT_ALGORITHMS:
            raise ValueError(f"JWT_ALGORITHM 仅支持 {'/'.join(_ALLOWED_JWT_ALGORITHMS)}")
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key_strength(cls, v: str) -> str:
        """HS256 密钥不得短于 32 字节（哈希输出长度）。

        短密钥会显著削弱签名强度，PyJWT 也会对 <32 字节的 HMAC 密钥发出
        InsecureKeyLengthWarning。此处对所有模式生效（含 DEV）：开发默认值本身
        已有 46 字节，正常使用不会触发；显式配了短密钥则应尽早失败而不是带病运行。
        """
        if len(v.encode()) < _MIN_SECRET_KEY_BYTES:
            raise ValueError(f"SECRET_KEY 至少需要 {_MIN_SECRET_KEY_BYTES} 字节")
        return v

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
    def docs_enabled(self) -> bool:
        """是否开放 API 文档：显式设置优先，否则 DEV 开、PROD 关。"""
        if self.ENABLE_DOCS is not None:
            return self.ENABLE_DOCS
        return self.MODE != "PROD"

    @property
    def cookie_secure(self) -> bool:
        """Secure Cookie 开关：显式设置优先，否则 PROD 开、DEV 关。"""
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.MODE == "PROD"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def SQLALCHEMY_DATABASE_URL(self) -> str:
        """同步连接串（mysql+pymysql），仅供 Alembic 迁移使用。"""
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    @property
    def SQLALCHEMY_ASYNC_DATABASE_URL(self) -> str:
        """异步连接串（mysql+asyncmy），应用运行时使用。"""
        return (
            f"mysql+asyncmy://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )


settings = Settings()
