# AGENTS.md

本文件供 AI 编码代理阅读，描述 `fastapi_app` 项目的架构、约定与开发流程。项目的主要文档、注释和提交信息均使用中文。

## 项目概述

`fastapi_app` 是一个基于 FastAPI + Vue 3 的生产级全栈脚手架项目：分层架构、JWT 认证、MySQL + Redis、Alembic 迁移、统一响应格式、全局异常处理、可插拔中间件能力，以及开箱即用的测试与 CI。前端位于 `frontend/`（Vue 3 + Vite + TS + Pinia + Element Plus），开发期经 Vite proxy 调后端，生产期由 FastAPI 托管 `frontend/dist`。

### 技术栈

- **框架**：FastAPI ≥ 0.115（`fastapi[standard]`，含 `fastapi` CLI）、Pydantic v2、pydantic-settings
- **前端**：Vue 3 + Vite + TypeScript + Pinia + Vue Router + Element Plus + VueUse + Axios（`frontend/`，npm 管理）
- **数据库**：MySQL 8（SQLAlchemy 2.0 + asyncmy，AsyncSession 全异步链路）、Alembic 迁移（仍走 PyMySQL 同步引擎，见 `alembic/env.py`）
- **缓存**：Redis（`redis.asyncio`）
- **认证**：PyJWT（HS256）+ pwdlib Argon2 密码哈希
- **可选能力**：slowapi（限流）、prometheus-fastapi-instrumentator（指标）
- **工具链**：uv 包管理、ruff、mypy、pytest、pre-commit、Docker / Docker Compose
- **Python**：`.python-version` 固定 3.12，`pyproject.toml` 要求 `>=3.10`，ruff/mypy 目标 py310

## 目录结构与模块分层

```
├── app/
│   ├── main.py              # 应用入口：lifespan（Redis 初始化）、异常处理、CORS、插件、路由注册
│   ├── core/                # 核心设施（与业务无关）
│   │   ├── settings.py      # Settings（pydantic-settings），环境变量/.env 覆盖，PROD 强制密钥
│   │   ├── database.py      # SQLAlchemy 异步引擎、AsyncSessionLocal、get_db 依赖（AsyncSession）
│   │   ├── redis.py         # create_redis() 客户端工厂
│   │   ├── security.py      # hash/verify 密码、create/decode_access_token
│   │   ├── deps.py          # get_current_user/get_current_member（async 依赖：黑名单 + 主体撤销 + CSRF 校验）
│   │   ├── revocation.py    # 主体级令牌撤销标记（Redis）：账号被删/停用时作废其全部令牌
│   │   ├── result.py        # 统一响应 Result[T] 泛型与 ok()/failure() 工厂
│   │   ├── middleware.py    # CORS 中间件 + 响应头中间件（静态缓存策略与安全头）
│   │   ├── static.py        # SPAStaticFiles：托管 frontend/dist，404 回退 index.html
│   │   └── exceptions.py    # 全局异常处理（统一响应，内部细节只进日志）
│   ├── api/                 # 路由层：auth.py、user.py、member.py、audit.py、health.py、index.py；业务路由统一挂 /api/v1 前缀
│   ├── login_guard.py       # 登录防爆破：按账号（可选 IP）统计连续失败并临时锁定
│   ├── services/            # 业务逻辑层，事务边界在此（负责 commit）
│   ├── repositories/        # 数据访问层，只查询/flush，绝不 commit
│   ├── models/              # SQLAlchemy ORM 模型（__init__.py 统一导入供 Alembic 发现）
│   ├── schemas/             # Pydantic 请求/响应模型
│   └── plugins/             # 可插拔能力，每个模块实现 setup(app)，按配置开关挂载
│       ├── __init__.py      # setup_plugins(app)：request_id / rate_limit / metrics / i18n
│       ├── i18n.py          # 国际化：语种解析 + 词条表 + /api/v1/i18n 接口
│       └── cache.py         # Redis 缓存装饰器 @cached(prefix, ttl)（始终可用，不是开关插件）
├── frontend/                # Vue 3 + Vite + TS 前端
│   ├── src/api/             # axios 封装（请求带 Bearer token，响应解包 Result，401 跳登录）
│   ├── src/stores/          # Pinia 认证 store（仅存登录用户信息；令牌在 HttpOnly Cookie 中，前端不持有）
│   ├── src/router/          # 路由 + 登录守卫
│   ├── src/views/           # Login / Register / Home / 用户管理 / 会员管理（只读列表）页面
│   └── vite.config.ts       # dev proxy：/api → 127.0.0.1:8000
├── alembic/                 # 数据库迁移（env.py 从 Settings 读连接串）
├── tests/                   # pytest（conftest 用 SQLite 内存库覆盖 get_db，FakeRedis 替换 create_redis）
├── pyproject.toml           # 依赖、ruff/mypy/pytest 配置、[tool.fastapi] entrypoint
├── Dockerfile               # 多阶段：node 构建前端 + python:3.12-slim 后端，非 root，HEALTHCHECK /health
├── docker-compose.yml       # 开发依赖：MySQL 8.4 + Redis 7
├── docker-compose.prod.yml  # 生产：依赖 + 应用容器（MODE=PROD）
├── .github/workflows/ci.yml # CI：ruff → pytest → docker build
└── .pre-commit-config.yaml  # ruff --fix + ruff-format
```

### 分层约定（必须遵守）

1. **api 层**：只做参数解析与响应包装，不写业务逻辑。新增路由只需在 `app/api/` 下新建模块并定义模块级 `router = APIRouter(...)` —— `register_routers` 会自动发现并注册，无需改其他文件。`/api/v1` 版本前缀由 `register_routers` 统一添加（`API_V1_PREFIX`），模块内不写版本号；`/health` 等运维端点不参与版本化，保留在根路径。可选业务模块（如 `member`）在 `register_routers` 的 `_MODULE_SWITCHES` 中登记开关，关闭时跳过路由注册（模型仍始终导入，保证迁移稳定）。
2. **service 层**：业务逻辑和事务边界（`await db.commit()` 只能出现在这里）。
3. **repository 层**：纯数据访问，使用 `await db.flush()`，**不允许 commit**。注意：`onupdate` 列（如 `updated_at`）flush 后属性过期，async 下惰性刷新会抛 `MissingGreenlet`，更新后需 `await db.refresh(obj)` 再返回。
4. **model 层**：ORM 模型是数据库 schema 的唯一来源；新模型必须在 `app/models/__init__.py` 中导入，Alembic autogenerate 才能发现。
5. **异步链路**：应用运行时全异步（asyncmy + `AsyncSession`），所有 repository/service 函数均为 `async def`；`AsyncSessionLocal` 必须保持 `expire_on_commit=False`（commit 后路由还要读字段序列化响应）。Alembic 迁移例外，仍用 PyMySQL 同步引擎。
6. **时间源统一为应用侧 UTC**：写入任何时间列（`created_at`/`updated_at`/`deleted_at` 及业务新增的时间字段）都用 `app.models.base.utc_now()`，不要用 `func.now()` / `datetime.now()`。`server_default` 只作手工 SQL 的兜底——数据库服务器时区非 UTC 时，库内生成的时间与应用生成的 UTC 会相差若干小时，按时间范围过滤软删记录就会出错。
7. **路径风格统一为 REST**：所有业务路由由 `register_routers` 自动挂 `/api/v1` 版本前缀（`app/api/__init__.py` 的 `API_V1_PREFIX`，出 v2 时改这一处）；`/health`、`/metrics`、`/docs` 等运维端点不参与版本化，保留在根路径。集合资源用斜杠结尾（`GET /api/v1/user/`、`POST /api/v1/user/`、`GET /api/v1/member/`），单个资源用路径参数（`GET/PATCH/DELETE /api/v1/user/{user_id}`）；认证与验证这类动作保留动词路径（`/api/v1/auth/login`、`/api/v1/member/verify-email`）。**禁止新增 RPC 风格的 `/xxx/list`、`/xxx/get_xxx`、`/xxx/create_xxx`**。新增带路径参数的路由时，注意把固定段路由（`/api/v1/user/me`）注册在它前面，否则固定段会被路径参数先行捕获（无法转 int 即 422）。

### 统一响应格式

所有接口返回 `Result[T]`（`app/core/result.py`）：

```json
{"code": 200, "data": {}, "message": "请求成功"}
```

- 路由声明 `response_model=Result[XxxOut]`，Swagger 可见真实结构。
- 成功用 `result.ok(data=..., message=...)`；业务失败优先 `raise HTTPException(...)`，由 `app/core/exceptions.py` 的全局处理器统一转为 `Result` 格式（校验错误 422、HTTP 异常、Redis/数据库/未知异常均已被接管，内部细节只写日志不外泄）。

## 配置

配置集中在 `app/core/settings.py` 的 `Settings`（pydantic-settings），全部可通过环境变量或 `.env`（参考 `.env.example`，`.env` 已被 gitignore）覆盖。关键项：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MODE` | `DEV` | 去空白并转大写后比较（大小写不敏感）；`PROD` 时强制要求显式设置 `SECRET_KEY` 和 `MYSQL_PASSWORD`，否则启动即报错 |
| `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DATABASE` | 127.0.0.1/3306/root/123456/fastapi_db | PROD 下 host 默认 docker 服务名 `mysql`；凭据在拼连接串时会做 URL 编码 |
| `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/`DB_POOL_RECYCLE`/`DB_POOL_TIMEOUT` | 10/20/3600/30 | 应用运行时连接池参数；`DB_POOL_RECYCLE` 需小于 MySQL `wait_timeout` |
| `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD` | 127.0.0.1/6379/无 | PROD 下 host 默认 `redis` |
| `SECRET_KEY` / `ACCESS_TOKEN_EXPIRE_MINUTES` / `JWT_ALGORITHM` | 开发默认值 / 30 / HS256 | JWT 签名配置；**`SECRET_KEY` 至少 32 字节**（HS256 安全下限，所有模式生效），`JWT_ALGORITHM` 仅允许 HS256/HS384/HS512，access token 含 `iat`/`exp`/`aud`/`jti`/`type="access"` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 7 | refresh token 有效期（天），含 `type="refresh"` 标记与 `jti` |
| `COOKIE_SECURE` | 未设置（PROD 开、DEV 关） | 认证 Cookie 的 Secure 标记；显式设置则以设置为准 |
| `METRICS_TOKEN` | 未设置（公开） | 配置后访问 `/metrics` 必须携带 `Authorization: Bearer <token>`，否则 401 |
| `ENABLE_DOCS` | 未设置（DEV 开、PROD 关） | API 文档（`/docs`、`/openapi.json`、`/redoc`）开关，显式设置则以设置为准 |
| `ALLOW_BOOTSTRAP` | 未设置（DEV 开、PROD 关） | 空库免鉴权创建首个管理员的开关；PROD 默认关闭，避免服务启动后被抢建管理员 |
| `REQUIRE_EMAIL_VERIFICATION` / `EMAIL_VERIFY_TOKEN_EXPIRE_HOURS` / `EMAIL_VERIFY_URL_BASE` | `false` / 24 / http://localhost:5173/verify-email | 邮箱验证；关闭时不强制验证即可登录。邮件投递实现见 `app/services/mailer.py`（默认只记日志，接真实服务时替换） |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_SECONDS` / `LOGIN_IP_MAX_FAILURES` | `5` / `900` / `0` | 登录防爆破（`app/login_guard.py`）：同一账号连续失败达阈值后锁定；IP 维度默认关闭；计数存 Redis，Redis 不可用时 fail-open |
| `CORS_ORIGINS` | `http://localhost,http://localhost:8080` | 逗号分隔（methods 固定 GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS——PATCH 必须列出，否则分域部署预检失败；headers 固定 Authorization/Content-Type/X-Request-ID/X-Requested-With）；**不支持通配符 `*`**，因本服务启用 `allow_credentials` |
| `ENABLE_RATE_LIMIT` / `RATE_LIMIT` | `false` / `100/minute` | slowapi 限流插件 |
| `ENABLE_METRICS` | `false` | Prometheus `/metrics` 插件 |
| `ENABLE_REQUEST_ID` | `true` | 请求 ID 中间件 + 日志串联插件 |
| `ENABLE_I18N` | `true` | 国际化插件开关：按请求解析语种并翻译统一响应文案（详见「国际化」章节） |
| `I18N_LOCALES` / `I18N_DEFAULT_LOCALE` | `zh-CN,en-US` / `zh-CN` | 支持的语种与默认语种 |
| `ENABLE_MEMBER` | `true` | 会员（C 端用户）模块开关，关闭后 `/api/v1/member` 路由不注册（模型与表结构保留） |
| `ENABLE_TASKS` / `TASK_BACKEND` / `TASK_WORKERS` | `false` / `memory` / `4` | 异步任务插件：开关、后端（memory 进程内 / redis 分布式）、worker/消费者数 |
| `TASK_STREAM` / `TASK_GROUP` / `TASK_RESULT_TTL` / `TASK_MAX_RETRIES` | `task:jobs` / `task:workers` / `86400` / `3` | redis 后端：Stream 键与消费组、结果哈希 TTL、失败重试次数 |
| `TASK_BLOCK_MS` / `TASK_CLAIM_MIN_IDLE_MS` / `TASK_RECLAIM_INTERVAL` | `5000` / `60000` / `30.0` | redis 后端：阻塞读取毫秒、pending 回收空闲阈值、sweeper 扫描间隔 |
| `TASK_TASKS_MODULE` / `TASK_CONSUMER_NAME` | `app.tasks` / 空 | redis 后端：具名任务定义模块、worker 消费者名前缀（空=主机名-pid） |
| `FRONTEND_DIST_DIR` | `frontend/dist` | 前端构建产物目录，存在时由后端托管为 SPA（API 路由优先匹配） |

## 构建与运行命令

包管理一律使用 **uv**（不要用 pip 直接装）；前端在 `frontend/` 下使用 **npm**：

```bash
docker compose up -d                  # 启动 MySQL + Redis（本地开发依赖）
uv sync                               # 安装/同步依赖（含 dev 组）
uv add <package>                      # 添加依赖
uv run alembic upgrade head           # 初始化/更新数据库表
uv run fastapi dev                    # 开发模式运行（自动重载，入口由 [tool.fastapi] 指定）
cd frontend && npm install && npm run dev    # 前端开发（localhost:5173，API 走 Vite proxy）
cd frontend && npm run build          # 前端构建，产出 frontend/dist（存在时由后端自动托管）
```

启动后：Swagger UI 在 `http://127.0.0.1:8000/docs`（PROD 默认关闭）。健康检查分三个端点：`/health/live` 只探进程存活（容器 HEALTHCHECK 用这个，依赖抖动不应导致重启）、`/health/ready` 与 `/health` 探 mysql/redis 连通性（组件全部正常时 200 + `status=ok`，任一组件 down 时 HTTP 503 + `status=degraded`，body 均为 Result 格式）。

**RBAC 种子数据是增量的**：`ensure_seeded` 只在超级管理员角色不存在时播种，新增权限点不会自动进入存量库。新增/调整种子权限后，部署时需跑一次幂等补种（会一并失效用户权限缓存）：

```bash
uv run python scripts/seed_rbac.py
```

## 认证与安全模型

- **双令牌**：access token（默认 30 分钟，`type="access"`、`jti`）+ refresh token（默认 7 天，`type="refresh"`、`jti`），均含 `aud`（`admin`/`member`）隔离身份。`create_access_token(subject, aud, expires_minutes=None)` / `create_refresh_token(subject, aud)`。
- **登录**（`/api/v1/auth/login`、`/api/v1/member/login`）返回 `TokenOut{access_token,refresh_token,token_type,expires_in}`，并种下双 HttpOnly Cookie（`access_token`/`refresh_token`，`path=/`、`samesite=lax`，Secure 按 `COOKIE_SECURE`：PROD 开、DEV 关）。**防爆破**：`app/login_guard.py` 按账号（可选 IP）统计连续失败，达 `LOGIN_MAX_FAILURES` 后锁定 `LOGIN_LOCKOUT_SECONDS` 秒并返回 429；登录成功清零账号计数；计数存 Redis，Redis 不可用时 fail-open（宁可放行也不让全站登录不可用）。
- **刷新**（`/api/v1/auth/refresh`、`/api/v1/member/refresh`）：cookie 优先、body `{"refresh_token":...}` 兜底；每次轮换删除旧 refresh `jti`（Redis `refresh:{jti}`），旧 token 重用 401；access token 冒充 refresh 因 `type != "refresh"` 被 401。
- **令牌类型隔离**（`app/core/deps.py::_resolve_subject`）：受保护接口只接受 `type` 为 `"access"` 或缺省（历史令牌）的令牌，`type="refresh"` 及其他未知类型一律 401。这是必须的——登出只把 access 的 `jti` 写黑名单、refresh 的 `jti` 仅从 `refresh:{jti}` 删除，而该集合在校验访问令牌时不会被查询，因此若放行 refresh 令牌，登出后凭它仍可完成特权写操作。
- **登出**（`/api/v1/auth/logout`、`/api/v1/member/logout`）：access `jti` 写入 Redis 黑名单（`blacklist:{jti}`，TTL=剩余有效期），refresh `jti` 删除，并清空双 Cookie。登出**只作用于当前设备**，不影响该账号在其他设备上的会话。
- **账号级撤销**（`app/core/revocation.py`）：管理员 `DELETE /api/v1/user/{user_id}` 或 `PATCH /api/v1/user/{user_id}` 置 `is_active=False`、以及会员 `DELETE /api/v1/member/me` 时，调用 `revoke_subject(aud, subject, redis)` 写一条撤销时间点（`revoke:{aud}:{subject}`，TTL=refresh 有效期）。`deps._resolve_subject` 与 `rotate_refresh_token` 都会校验：**签发时间（`iat`）不晚于撤销时间点的令牌一律 401**。这样一次写入即可作废该主体在**所有设备**上的全部令牌（access + refresh）——逐 jti 撤销做不到这点，因为 jti 无法由主体反查；且账号重新启用后旧会话不会复活。`iat` 为秒级，故用 `<=` 比较（宁可误杀撤销后同秒签发的令牌，也不漏放）。登出**不应**调用它，否则会误踢该账号的其他设备。
- **取令牌顺序**（`app/core/deps.py`）：Authorization Bearer 优先，其次 `access_token` Cookie。**CSRF 防护**：Cookie 来源的写请求（POST/PUT/PATCH/DELETE）必须带 `X-Requested-With: XMLHttpRequest`，否则 403；Bearer 来源不受此约束；黑名单命中 401；Redis 异常 fail-open 记日志。
- **依赖注入**：`get_current_user`/`get_current_member` 是 **async def**，FastAPI DI 原生支持；`/api/v1/user/` 自举守卫在 async 端点内手动 `await get_current_user(request=..., credentials=..., db=..., redis=...)`（传入 request 以保留 Cookie 令牌与 CSRF 校验），禁止再用同步事件循环变通。
- **软删除**：`SoftDeleteMixin`（`deleted_at`，indexed），repository 统一过滤未删除记录；软删不释放邮箱唯一约束（同邮箱再注册 409，并发撞唯一约束由 service 层 IntegrityError 兜底转 409）。

## 关键接口语义

- `POST /api/v1/auth/bootstrap`（初始化首个管理员）：空库且 `ALLOW_BOOTSTRAP` 打开时免鉴权创建首个管理员，自动播种 RBAC 并授予 `super_admin`；已初始化返回 409，开关关闭返回 403。前端通过 `GET /api/v1/info` 的 `needs_bootstrap` 决定是否展示该入口。
- `POST /api/v1/user/`（日常创建后台账号）：**恒需**有效 admin 令牌 + `user:write` 权限，否则 401/403。与自举端点分离，避免空库状态下退化为免鉴权入口。密码策略：8-64 位且同时包含字母和数字；name 2-32 位、去空白后非空；email 统一 `lower().strip()` 归一化。
- `GET /api/v1/audit/logs`：需 `audit:read` 权限（审计日志含操作者与来源 IP，只校验登录态会让任意后台账号读到全量）。种子权限为 `user:read/write`、`member:read/write`、`role:read/write`、`permission:read`、`audit:read`；`admin` 含 `audit:read`，`viewer` 不含。前端权限码与后端必须同步，且菜单/路由守卫用 `permissionStore.can()`（含 super_admin 放行），不要用 `has()`。
- 登录（`/api/v1/auth/login`、`/api/v1/member/login`）：账号不存在、密码错误、账号禁用（`is_active=False`）一律 401，不泄露账号状态；用户不存在时服务端做 dummy 哈希校验拉平响应耗时。
- 邮箱撞唯一约束（含并发、软删后重建）返回 409。
- 分页：`GET /api/v1/user/`、`GET /api/v1/member/`（均 admin 鉴权）返回 `Result[Page[T]]`（`total`/`items`/`page`/`page_size`）；参数 `page≥1`、`page_size` 1-100 默认 20，越界 422。
- 用户管理（admin）：`PATCH /api/v1/user/{user_id}`（`UpdateUser{name?,is_active?}`，不存在 404）；`DELETE /api/v1/user/{user_id}`（软删，删自己 400，不存在 404）。停用与软删都会撤销该账号全部令牌（见「账号级撤销」），仅改 name 不会。
- 会员自助：`PATCH /api/v1/member/me`（`UpdateMember{nickname?}`）；`DELETE /api/v1/member/me`（软删并撤销令牌，含该账号其他设备）。
- `GET /api/v1/user/{user_id}`、`GET /api/v1/member/me`、`GET /api/v1/user/me`：目标不存在或已软删时 404/401（会员被删后令牌即失效）。

## 代码风格

- **ruff** 负责 lint + format：`line-length = 100`，`target-version = py310`，启用规则集 `E, F, I, UP, B, ASYNC, SIM`。
- `B008` 被有意忽略：FastAPI 依赖注入惯用法 `Depends()` 必须写在参数默认值里。
- mypy 非 strict、`ignore_missing_imports = true`，排除 `alembic/versions/`。
- 使用现代类型标注（`X | None` 而非 `Optional[X]`，ruff UP 规则强制）；Python 3.10+ 语法。
- ORM 使用 SQLAlchemy 2.0 风格（`Mapped[...]` + `mapped_column`）。
- 提交前检查：`uv run ruff check .` 和 `uv run ruff format .`；可选 `pre-commit install` 自动执行。
- 注释和文档字符串使用中文。

## 前端约定

- **自动导入**：`vite.config.ts` 用 `unplugin-auto-import` 自动导入 `vue` / `vue-router` / `pinia` / `@vueuse/core` 的 API，以及 `src/composables/` 下的项目自定义 composable。组件里直接用 `ref` / `computed` / `useStorage` / `useAppTheme` 等即可，**不要**再写这些来源的 import；Element Plus、api、stores、utils 仍需显式 import。
- **类型声明入库**：自动导入清单生成到 `src/auto-imports.d.ts`，该文件必须提交——`build` 先跑 `vue-tsc` 再跑 `vite build`，文件缺失会导致类型检查失败。新增 composable 或改动自动导入范围后，重新构建即可刷新它。
- **VueUse 用法约定**：
  - 本地持久化一律用 `useStorage`，键名统一以 `admin.` 前缀（如 `admin.sidebar.collapsed`、`admin.color-scheme`）。
  - 轮询用 `useIntervalFn`，并配合 `useConnectionStatus`（`useOnline` + `useWindowFocus`）在离线或窗口失焦时 `pause()`，回到前台再 `resume()`。
  - 筛选类输入用 `watchDebounced` 防抖后自动查询，不要手写 `setTimeout` 防抖。
  - 暗色模式统一走 `useAppTheme`（内部 `useDark` + `useToggle`），在 `<html>` 上挂 `dark` 类；Element Plus 组件配色由 `theme-chalk/dark/css-vars.css` 自动切换，**自定义类里的颜色必须写成 Element Plus 的 CSS 变量**（`var(--el-bg-color)`、`var(--el-border-color-lighter)` 等），写死十六进制在暗色下不会跟随。
  - 页面标题由根组件 `App.vue` 调用 `useDocumentTitle` 统一维护，不要在路由守卫里直接改 `document.title`。

## 测试

```bash
uv run pytest        # 测试目录 tests/，无需 MySQL/Redis
```

- `tests/conftest.py` 用 SQLite 内存库（aiosqlite + `StaticPool`）通过 `app.dependency_overrides` 覆盖 `get_db`；每个测试后自动清空所有表（autouse fixture）；测试内需直接查库/造数时用 `run_db(async_fn)` 同步包装执行异步操作。
- Redis 由 conftest 中的内存 `FakeRedis` 隔离（autouse monkeypatch 替换 `app.main.create_redis`，`/health` 使用的引擎同步替换为测试库），测试不依赖真实 MySQL/Redis；`broken_redis` fixture 可将 Redis 置为故障态，供 degraded 用例使用。
- 测试通过 `TestClient`（`client` fixture）走完整 HTTP 链路，覆盖认证、校验错误、统一响应格式等。
- 新增功能应在 `tests/` 下补充对应 API 测试，断言统一响应的 `code`/`data` 字段。
- pytest 默认带 `--cov=app`（addopts 已配置，coverage 门槛见 CI 的 `--cov-fail-under`）。

## 数据库迁移

模型是 schema 的唯一来源，不要手写 SQL 建表：

```bash
uv run alembic revision --autogenerate -m "add xxx"
uv run alembic upgrade head
```

`alembic/env.py` 从 `Settings.SQLALCHEMY_DATABASE_URL` 读连接串，迁移环境与运行配置自动一致。

## 插件开发约定

`app/plugins/` 下每个模块是一个可选能力。新增插件步骤：新建模块 → 实现 `setup(app)` → 在 `setup_plugins`（`app/plugins/__init__.py`）中按开关登记 → 在 `Settings` 加 `ENABLE_XXX` 开关。现有插件：`request_id`（请求 ID + 日志 Filter；X-Request-ID 仅接受 `^[A-Za-z0-9_-]{1,64}$`，非法值自动生成 uuid）、`rate_limit`（slowapi 限流，超限返回统一格式 429。**不使用 slowapi 自带的 SlowAPIMiddleware**：FastAPI 0.141 起 `include_router` 注册的是没有 `endpoint` 属性的 `_IncludedRouter`，slowapi 的 `_find_route_handler` 查找恒为 None 从而把每个请求判为豁免，导致限流在真实应用上整体静默失效；且它无法 await 协程处理器时会回退到默认 body，泄露限流阈值。现改为自建中间件，按「是否命中 API 路由」判定并直接调用 `limiter._check_request_limit`，SPA/静态资源豁免，详见 `app/plugins/rate_limit.py` 注释与 `tests/test_rate_limit.py`）、`metrics`（Prometheus `/metrics`；配置 `METRICS_TOKEN` 后要求 `Bearer <token>`，否则 401，未配置保持公开）、`tasks`（异步任务执行器，`TASK_BACKEND` 二选一：`memory`=进程内有界队列+N 个 worker 协程，单机 Fire-and-forget、重启即丢；`redis`=Redis Streams 消费组，分布式/跨进程/可恢复/失败重试。公共 API `task_manager.submit(handler,*args)` / 先 `@task_manager.task("名")` 注册再 `submit_named("名",*args)`；`GET /api/v1/tasks` 观测（memory 返回 recent，redis 传 `?job_id=` 查单条）。**redis 后端只支持具名任务**：任务须跨进程序列化，本地闭包无法投递（`submit` 在 redis 模式抛错），处理器定义在 `TASK_TASKS_MODULE`（默认 `app.tasks`）。memory 后端的 worker 启停在 `app/main.py` 的 lifespan；redis 后端的 Web 进程只投递/查询，**消费由独立 worker 进程完成**：`python -m app.plugins.tasks_worker`（可多进程/多机扩容，`-c` 指定单进程消费者数）。可靠投递为 at-least-once：执行成功/最终失败才 XACK，掉线消费者遗留的 pending 由 sweeper 用 XAUTOCLAIM 抢回重投，处理器需幂等）、`cache`（`@cached("user", ttl=300)` 装饰器，Redis 未初始化时自动回退直调，无需开关）。

**异步任务插件的队列必须在运行时循环创建**：`TaskManager` 在 `start()`（而非 `__init__`）里惰性创建 `asyncio.Queue`，否则队列会绑定到模块导入时的事件循环，而应用实际运行在另一个循环（TestClient 后台循环 / uvicorn 循环），worker 一操作队列就抛 `bound to a different event loop`。详见 `app/plugins/tasks.py` 注释。

## 部署

```bash
docker build -t fastapi_app .
docker compose -f docker-compose.prod.yml up -d          # 需先在 .env 设置 SECRET_KEY 与 MYSQL_PASSWORD
docker compose -f docker-compose.prod.yml exec fastapi_app alembic upgrade head   # 首次部署执行迁移
```

Dockerfile：多阶段构建——`node:22-alpine` 阶段 `npm ci && npm run build` 产出前端 dist，`python:3.12-slim` + uv 阶段 `uv sync --frozen --no-dev` 并复制 dist（由后端托管）；非 root 用户运行，HEALTHCHECK 打 `/health`，启动命令 `fastapi run app/main.py`。

CI（GitHub Actions，push main 或 PR）：`uv sync --frozen` → `ruff check` + `ruff format --check` → `mypy app tests` → `pytest --cov=app --cov-fail-under=82` → 前端 `npm ci && npm run build` → `docker build`。依赖版本由 renovate 自动跟踪（`renovate.json`）。

## 国际化（i18n 插件）

`app/plugins/i18n.py`，由 `ENABLE_I18N` 开关控制（默认开启；关闭时行为与未集成完全一致，中文环境返回内容逐字不变）。

- **词条键就是中文原文**（如 `"邮箱或密码错误"`）。这样业务代码 `raise HTTPException(detail="...")` 的写法不变，中文环境返回内容逐字不变；新增语种只需在 `CATALOGS` 里补一份映射。
- **语种解析顺序**：`?locale=` → `Accept-Language`（按 q 权重）→ `I18N_DEFAULT_LOCALE`；解析结果写入 `ContextVar` 并回写 `Content-Language` 响应头，请求结束后清理（连接复用不会串味）。
- **翻译链路**：`app/core/exceptions.py` 的异常处理器统一走 `_t()`；成功文案需显式 `result.ok(..., translate=True)`（默认不翻译，避免给所有接口加开销）。缺词条、翻译异常一律回退中文原文。
- **带运行时数值的文案不要先拼好**：`f"请 {minutes} 分钟后重试"` 一旦拼成字符串就再也匹配不上词条表里的模板，切语言时永远回中文。约定写成 `detail="请 {minutes} 分钟后重试|" + json.dumps({"minutes": minutes})`，由 `translate_exception_detail()` 拆出参数后再插值（例：`app/login_guard.py`、`app/core/rbac.py`）。
- **接口**：`GET /api/v1/i18n/locales`（支持语种）、`GET /api/v1/i18n/messages?locale=en-US`（完整词条）。
- **前端**：`frontend/src/plugins/i18n/`（与后端同源的扁平词条 + `translate()` + 语言切换，未引 vue-i18n）。启动时会调 `syncFromBackend()` 用后端词条覆盖本地；插件未启用时接口 404，静默回退本地词条（已在 `api/request.ts` 的 `SILENT_404_PATHS` 登记，不弹错误提示）。
- **`api/request.ts` 的请求拦截器会显式下发 `Accept-Language: <当前语种>`**，保证后端返回的 `message` 与界面语种一致（不写 header 就会跟着浏览器语言走，切了界面语言后端仍是英文/中文不一）。
- 前端词条只维护 `locales/en-US.ts` 一份，中文词条 `locales/zh-CN.ts` 由其键推导（键即中文原文），避免两边漏同步露键名。
- 新增可见文案时：先写中文原文，再在 `CATALOGS['en-US']` 与前端 `locales/en-US.ts` 补译文。

## 安全注意事项

- **PROD 模式启动校验**：`MODE=PROD` 时未显式设置 `SECRET_KEY` 或 `MYSQL_PASSWORD` 会直接抛错拒绝启动——不要在生产环境绕过此校验。另外 `SECRET_KEY` 短于 32 字节在所有模式下都会被拒绝（HS256 安全下限）。
- 密码一律用 `security.hash_password`（Argon2）入库；响应模型（`UserOut` 等）不得包含 `password` 字段。
- JWT 密钥、数据库密码只通过环境变量/`.env` 注入，`.env` 不得提交；`.env.example` 只放开发默认值。
- 全局异常处理器不会把内部异常细节返回给客户端（只进日志），新增异常处理时保持这一原则。
- 受保护接口通过 `Depends(get_current_user)` 鉴权；令牌无效、用户不存在或 `is_active=False` 均返回 401。
- 后台用户与会员（C 端）分表分令牌：JWT 带 `aud` claim（`admin` / `member`），后台接口用 `Depends(get_current_user)`，会员接口用 `Depends(get_current_member)`，两类令牌互不通用（跨用返回 401）。此外 `type` claim 隔离访问与刷新令牌：受保护接口拒绝 `type="refresh"` 的令牌（`app/core/deps.py`）。
