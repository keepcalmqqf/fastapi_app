from typing import ClassVar
from urllib.parse import quote

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET_KEY = "dev-only-secret-key-please-change-in-production"

# 允许的 JWT 签名算法（对称 HMAC 系列，防止误配 none 导致签名校验失效）
_ALLOWED_JWT_ALGORITHMS = ("HS256", "HS384", "HS512")

# HS256 的安全下限：密钥长度不应低于哈希输出长度（32 字节）
_MIN_SECRET_KEY_BYTES = 32

# 生产模式标识（MODE 归一化后比较）：PROD 触发生产强校验与默认安全策略
_MODE_PROD = "PROD"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 限流存储显式回退到进程内存的取值（多 worker 下阈值会放大，仅供单机/测试使用）
    RATE_LIMIT_MEMORY_STORAGE_URI: ClassVar[str] = "memory://"
    # 限流计数所在的 Redis 库：与业务数据同库（redis-py 默认 db 0），
    # key 由 slowapi 自行加前缀，不会与 refresh token 等的 key 冲突
    RATE_LIMIT_REDIS_DB: ClassVar[int] = 0

    MODE: str = "DEV"

    # MySQL
    MYSQL_HOST: str = ""
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DATABASE: str = "fastapi_db"

    # 数据库连接池（仅应用运行时生效，Alembic 迁移走 NullPool 不受影响）
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    # 回收连接的时间阈值：需小于 MySQL wait_timeout，避免拿到已被服务端断开的连接
    DB_POOL_RECYCLE: int = 3600
    DB_POOL_TIMEOUT: int = 30

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

    # 邮箱验证：开关关闭时邮箱验证仅作留档，不影响登录
    REQUIRE_EMAIL_VERIFICATION: bool = False
    EMAIL_VERIFY_TOKEN_EXPIRE_HOURS: int = 24
    # 验证链接的基础地址（前端页面地址），邮件里拼成 {base}?token=xxx
    EMAIL_VERIFY_URL_BASE: str = "http://localhost:5173/verify-email"

    # 登录防爆破：同一账号连续失败达阈值后锁定若干秒；来源 IP 维度默认关闭，
    # 置为正数可同时限制同一出口的撞库（IP 层已有全局限流兜底，谨慎叠加）
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 900
    LOGIN_IP_MAX_FAILURES: int = 0

    # 空库自举开关（None 时 DEV 开、PROD 关）：关闭后建号一律要求有效后台令牌。
    # PROD 下开放意味着服务首次启动后任何人都能抢建管理员，故默认关闭。
    ALLOW_BOOTSTRAP: bool | None = None

    # CORS（逗号分隔，如 "http://localhost,http://localhost:8080"）
    CORS_ORIGINS: str = "http://localhost,http://localhost:8080"

    # 前端构建产物目录（存在时由后端托管为 SPA）
    FRONTEND_DIST_DIR: str = "frontend/dist"

    # 可插拔能力开关
    ENABLE_RATE_LIMIT: bool = False
    RATE_LIMIT: str = "100/minute"
    # 限流计数存储：留空时自动推导为共享 Redis（见 rate_limit_storage_uri）。
    # 多 worker 部署下进程内存计数各进程独立，实际阈值放大为 RATE_LIMIT × worker 数
    # （实测 2 worker + 100/minute ≈ 200/minute），故默认指向 Redis 让限额全局生效。
    # 确需内存计数时显式设为 RATE_LIMIT_MEMORY_STORAGE_URI。
    RATE_LIMIT_STORAGE_URI: str | None = None
    ENABLE_METRICS: bool = False
    ENABLE_REQUEST_ID: bool = True
    ENABLE_MEMBER: bool = True

    @field_validator("MODE")
    @classmethod
    def _normalize_mode(cls, v: str) -> str:
        """运行模式归一化：去空白并转大写后再比较。

        此前 `MODE=prod`（小写）会被判为非 PROD，静默套用 DEV 策略——API 文档开放、
        Cookie 不带 Secure，PROD 的 SECRET_KEY / MYSQL_PASSWORD 强校验也一并被跳过。
        """
        return v.strip().upper()

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

        额外校验「去空白后的有效长度」：仅按原始长度判断时，40 个空格也能通过，
        实际熵为零。
        """
        if len(v.encode()) < _MIN_SECRET_KEY_BYTES:
            raise ValueError(f"SECRET_KEY 至少需要 {_MIN_SECRET_KEY_BYTES} 字节")
        if len(v.strip().encode()) < _MIN_SECRET_KEY_BYTES:
            raise ValueError("SECRET_KEY 不能由空白字符填充")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _reject_wildcard_cors_origin(cls, v: str) -> str:
        """禁止通配源 `*`：本服务 CORS 启用 allow_credentials=True，两者同用时
        Starlette 会反射任意 Origin，等价于对全网开放带凭证的跨域访问，
        令牌 Cookie 的 CSRF 防护随之失效。多源请显式逐条列出。
        """
        if "*" in [o.strip() for o in v.split(",")]:
            raise ValueError("CORS_ORIGINS 不支持通配符 '*'，请显式列出允许的源")
        return v

    @model_validator(mode="after")
    def _apply_mode_defaults(self) -> "Settings":
        is_prod = self.MODE == _MODE_PROD
        if not self.MYSQL_HOST:
            self.MYSQL_HOST = "mysql" if is_prod else "127.0.0.1"
        if not self.REDIS_HOST:
            self.REDIS_HOST = "redis" if is_prod else "127.0.0.1"
        if is_prod:
            if self.SECRET_KEY == _DEV_SECRET_KEY:
                raise ValueError("PROD 模式必须通过环境变量设置 SECRET_KEY")
            if self.MYSQL_PASSWORD == "123456":
                raise ValueError("PROD 模式必须通过环境变量设置 MYSQL_PASSWORD")
            # 空串比 None 更危险：compose 的 ${METRICS_TOKEN} 在 .env 漏配时会被替换成
            # 空串，鉴权判定为「未配置」从而静默把 /metrics 变成公开端点
            if self.ENABLE_METRICS and not self.METRICS_TOKEN:
                raise ValueError("PROD 模式开启指标采集时必须设置 METRICS_TOKEN")
        return self

    @property
    def docs_enabled(self) -> bool:
        """是否开放 API 文档：显式设置优先，否则 DEV 开、PROD 关。"""
        if self.ENABLE_DOCS is not None:
            return self.ENABLE_DOCS
        return self.MODE != _MODE_PROD

    @property
    def cookie_secure(self) -> bool:
        """Secure Cookie 开关：显式设置优先，否则 PROD 开、DEV 关。"""
        if self.COOKIE_SECURE is not None:
            return self.COOKIE_SECURE
        return self.MODE == _MODE_PROD

    @property
    def bootstrap_allowed(self) -> bool:
        """是否允许空库免鉴权创建首个管理员：显式设置优先，否则 DEV 开、PROD 关。

        PROD 下该口子等同于「谁先到谁就是管理员」，默认为关；确需自举时显式
        设 ALLOW_BOOTSTRAP=true 并在建号后立即重启关闭。
        """
        if self.ALLOW_BOOTSTRAP is not None:
            return self.ALLOW_BOOTSTRAP
        return self.MODE != _MODE_PROD

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def rate_limit_storage_uri(self) -> str:
        """限流计数存储连接串：显式配置优先，留空时自动推导为共享 Redis。

        默认走 Redis 是为了让多 worker 共用一份计数：进程内存模式下各 worker
        独立累加，实际阈值会放大成 RATE_LIMIT × worker 数，等于配了个假限额。
        需要内存计数（单机调试、测试）时显式设为 RATE_LIMIT_MEMORY_STORAGE_URI。
        """
        if self.RATE_LIMIT_STORAGE_URI:
            return self.RATE_LIMIT_STORAGE_URI
        return self._build_redis_url()

    def _build_redis_url(self) -> str:
        """按 REDIS_* 拼共享存储连接串（凭据 URL 编码，避免特殊字符拼出非法 URI）。"""
        # IPv6 地址在 URI 里必须带方括号，否则端口切分会把它截成两段
        host = f"[{self.REDIS_HOST}]" if ":" in self.REDIS_HOST else self.REDIS_HOST
        # 用 quote（而非 quote_plus）：加号在密码里是字面字符，不该被解成空格
        auth = f":{quote(self.REDIS_PASSWORD, safe='')}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{host}:{self.REDIS_PORT}/{self.RATE_LIMIT_REDIS_DB}"

    @property
    def SQLALCHEMY_DATABASE_URL(self) -> str:
        """同步连接串（mysql+pymysql），仅供 Alembic 迁移使用。

        用户名与密码经 URL 编码：密码里含 `@ / : # ?` 等字符时，未编码会拼出
        非法连接串（如 `p@ss/w:rd` 会被解析成别的主机与端口）。

        用 quote（而非 quote_plus）：userinfo 里的 `+` 是字面加号，不会被解成
        空格，密码含空格时 quote_plus 会把它错误地改写；quote 编成 `%20` 才正确。
        """
        return self._build_url("mysql+pymysql")

    def _build_url(self, drivername: str) -> str:
        user = quote(self.MYSQL_USER, safe="")
        password = quote(self.MYSQL_PASSWORD, safe="")
        return (
            f"{drivername}://{user}:{password}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )

    @property
    def SQLALCHEMY_ASYNC_DATABASE_URL(self) -> str:
        """异步连接串（mysql+asyncmy），应用运行时使用（凭据同样做 URL 编码）。"""
        return self._build_url("mysql+asyncmy")


settings = Settings()
