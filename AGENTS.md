# AGENTS.md

本文件供 AI 编码代理阅读，描述 `fastapi_app` 项目的架构、约定与开发流程。项目的主要文档、注释和提交信息均使用中文。

## 项目概述

`fastapi_app` 是一个基于 FastAPI + Vue 3 的生产级全栈脚手架项目：分层架构、JWT 认证、MySQL + Redis、Alembic 迁移、统一响应格式、全局异常处理、可插拔中间件能力，以及开箱即用的测试与 CI。前端位于 `frontend/`（Vue 3 + Vite + TS + Pinia + Element Plus），开发期经 Vite proxy 调后端，生产期由 FastAPI 托管 `frontend/dist`。

### 技术栈

- **框架**：FastAPI ≥ 0.115（`fastapi[standard]`，含 `fastapi` CLI）、Pydantic v2、pydantic-settings
- **前端**：Vue 3 + Vite + TypeScript + Pinia + Vue Router + Element Plus + Axios（`frontend/`，npm 管理）
- **数据库**：MySQL 8（SQLAlchemy 2.0 + PyMySQL，同步 Session）、Alembic 迁移
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
│   │   ├── database.py      # SQLAlchemy 引擎、SessionLocal、get_db 依赖
│   │   ├── redis.py         # create_redis() 客户端工厂
│   │   ├── security.py      # hash/verify 密码、create/decode_access_token
│   │   ├── deps.py          # get_current_user/get_current_member（async 依赖：黑名单 + CSRF 校验）
│   │   ├── result.py        # 统一响应 Result[T] 泛型与 ok()/failure() 工厂
│   │   ├── middleware.py    # CORS 中间件（origins 走配置）
│   │   ├── static.py        # SPAStaticFiles：托管 frontend/dist，404 回退 index.html
│   │   └── exceptions.py    # 全局异常处理（统一响应，内部细节只进日志）
│   ├── api/                 # 路由层：auth.py、user.py、health.py、index.py（/api/info）
│   ├── services/            # 业务逻辑层，事务边界在此（负责 commit）
│   ├── repositories/        # 数据访问层，只查询/flush，绝不 commit
│   ├── models/              # SQLAlchemy ORM 模型（__init__.py 统一导入供 Alembic 发现）
│   ├── schemas/             # Pydantic 请求/响应模型
│   └── plugins/             # 可插拔能力，每个模块实现 setup(app)，按配置开关挂载
│       ├── __init__.py      # setup_plugins(app)：request_id / rate_limit / metrics
│       └── cache.py         # Redis 缓存装饰器 @cached(prefix, ttl)（始终可用，不是开关插件）
├── frontend/                # Vue 3 + Vite + TS 前端
│   ├── src/api/             # axios 封装（请求带 Bearer token，响应解包 Result，401 跳登录）
│   ├── src/stores/          # Pinia 认证 store（token 持久化 localStorage）
│   ├── src/router/          # 路由 + 登录守卫
│   ├── src/views/           # Login / Register / Home / 用户管理 / 会员管理（只读列表）页面
│   └── vite.config.ts       # dev proxy：/auth /user /member /health → 127.0.0.1:8000
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

1. **api 层**：只做参数解析与响应包装，不写业务逻辑。新增路由只需在 `app/api/` 下新建模块并定义模块级 `router = APIRouter(...)` —— `register_routers` 会自动发现并注册，无需改其他文件。可选业务模块（如 `member`）在 `register_routers` 的 `_MODULE_SWITCHES` 中登记开关，关闭时跳过路由注册（模型仍始终导入，保证迁移稳定）。
2. **service 层**：业务逻辑和事务边界（`db.commit()` 只能出现在这里）。
3. **repository 层**：纯数据访问，使用 `db.flush()`，**不允许 commit**。
4. **model 层**：ORM 模型是数据库 schema 的唯一来源；新模型必须在 `app/models/__init__.py` 中导入，Alembic autogenerate 才能发现。

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
| `MODE` | `DEV` | `PROD` 时强制要求显式设置 `SECRET_KEY` 和 `MYSQL_PASSWORD`，否则启动即报错 |
| `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DATABASE` | 127.0.0.1/3306/root/123456/fastapi_db | PROD 下 host 默认 docker 服务名 `mysql` |
| `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD` | 127.0.0.1/6379/无 | PROD 下 host 默认 `redis` |
| `SECRET_KEY` / `ACCESS_TOKEN_EXPIRE_MINUTES` / `JWT_ALGORITHM` | 开发默认值 / 30 / HS256 | JWT 签名配置；`JWT_ALGORITHM` 仅允许 HS256/HS384/HS512，access token 含 `iat`/`exp`/`aud`/`jti` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 7 | refresh token 有效期（天），含 `type="refresh"` 标记与 `jti` |
| `COOKIE_SECURE` | 未设置（PROD 开、DEV 关） | 认证 Cookie 的 Secure 标记；显式设置则以设置为准 |
| `METRICS_TOKEN` | 未设置（公开） | 配置后访问 `/metrics` 必须携带 `Authorization: Bearer <token>`，否则 401 |
| `ENABLE_DOCS` | 未设置（DEV 开、PROD 关） | API 文档（`/docs`、`/openapi.json`、`/redoc`）开关，显式设置则以设置为准 |
| `CORS_ORIGINS` | `http://localhost,http://localhost:8080` | 逗号分隔（methods 固定 GET/POST/PUT/DELETE/OPTIONS，headers 固定 Authorization/Content-Type/X-Request-ID/X-Requested-With） |
| `ENABLE_RATE_LIMIT` / `RATE_LIMIT` | `false` / `100/minute` | slowapi 限流插件 |
| `ENABLE_METRICS` | `false` | Prometheus `/metrics` 插件 |
| `ENABLE_REQUEST_ID` | `true` | 请求 ID 中间件 + 日志串联插件 |
| `ENABLE_MEMBER` | `true` | 会员（C 端用户）模块开关，关闭后 `/member` 路由不注册（模型与表结构保留） |
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

启动后：Swagger UI 在 `http://127.0.0.1:8000/docs`（PROD 默认关闭），健康检查在 `/health`（返回 mysql/redis 连通性；组件全部正常时 200 + `status=ok`，任一组件 down 时 HTTP 503 + `status=degraded`，body 均为 Result 格式）。

## 认证与安全模型

- **双令牌**：access token（默认 30 分钟，`jti`）+ refresh token（默认 7 天，`type="refresh"`、`jti`），均含 `aud`（`admin`/`member`）隔离身份。`create_access_token(subject, aud, expires_minutes=None)` / `create_refresh_token(subject, aud)`。
- **登录**（`/auth/login`、`/member/login`）返回 `TokenOut{access_token,refresh_token,token_type,expires_in}`，并种下双 HttpOnly Cookie（`access_token`/`refresh_token`，`path=/`、`samesite=lax`，Secure 按 `COOKIE_SECURE`：PROD 开、DEV 关）。
- **刷新**（`/auth/refresh`、`/member/refresh`）：cookie 优先、body `{"refresh_token":...}` 兜底；每次轮换删除旧 refresh `jti`（Redis `refresh:{jti}`），旧 token 重用 401；access token 冒充 refresh 因缺 `type` 被 401。
- **登出**（`/auth/logout`、`/member/logout`）：access `jti` 写入 Redis 黑名单（`blacklist:{jti}`，TTL=剩余有效期），refresh `jti` 删除，并清空双 Cookie。
- **取令牌顺序**（`app/core/deps.py`）：Authorization Bearer 优先，其次 `access_token` Cookie。**CSRF 防护**：Cookie 来源的写请求（POST/PUT/PATCH/DELETE）必须带 `X-Requested-With: XMLHttpRequest`，否则 403；Bearer 来源不受此约束；黑名单命中 401；Redis 异常 fail-open 记日志。
- **依赖注入**：`get_current_user`/`get_current_member` 是 **async def**，FastAPI DI 原生支持；`/user/create_user` 自举守卫在 async 端点内手动 `await get_current_user(request=..., credentials=..., db=..., redis=...)`（传入 request 以保留 Cookie 令牌与 CSRF 校验），禁止再用同步事件循环变通。
- **软删除**：`SoftDeleteMixin`（`deleted_at`，indexed），repository 统一过滤未删除记录；软删不释放邮箱唯一约束（同邮箱再注册 409，并发撞唯一约束由 service 层 IntegrityError 兜底转 409）。

## 关键接口语义

- `POST /user/create_user`（空库自举）：users 表为空时无需 token 即可创建首个管理员；表非空时必须持有有效 admin 令牌（Bearer 或 Cookie），否则 401。密码策略：8-64 位且同时包含字母和数字；name 2-32 位、去空白后非空；email 统一 `lower().strip()` 归一化。
- 登录（`/auth/login`、`/member/login`）：账号不存在、密码错误、账号禁用（`is_active=False`）一律 401，不泄露账号状态；用户不存在时服务端做 dummy 哈希校验拉平响应耗时。
- 邮箱撞唯一约束（含并发、软删后重建）返回 409。
- 分页：`GET /user/list`、`GET /member/list`（均 admin 鉴权）返回 `Result[Page[T]]`（`total`/`items`/`page`/`page_size`）；参数 `page≥1`、`page_size` 1-100 默认 20，越界 422。
- 用户管理（admin）：`PATCH /user/{user_id}`（`UpdateUser{name?,is_active?}`，不存在 404）；`DELETE /user/{user_id}`（软删，删自己 400，不存在 404）。
- 会员自助：`PATCH /member/me`（`UpdateMember{nickname?}`）；`DELETE /member/me`（软删并撤销令牌）。
- `GET /user/get_user`、`GET /member/me`、`GET /user/me`：目标不存在或已软删时 404/401（会员被删后令牌即失效）。

## 代码风格

- **ruff** 负责 lint + format：`line-length = 100`，`target-version = py310`，启用规则集 `E, F, I, UP, B, ASYNC, SIM`。
- `B008` 被有意忽略：FastAPI 依赖注入惯用法 `Depends()` 必须写在参数默认值里。
- mypy 非 strict、`ignore_missing_imports = true`，排除 `alembic/versions/`。
- 使用现代类型标注（`X | None` 而非 `Optional[X]`，ruff UP 规则强制）；Python 3.10+ 语法。
- ORM 使用 SQLAlchemy 2.0 风格（`Mapped[...]` + `mapped_column`）。
- 提交前检查：`uv run ruff check .` 和 `uv run ruff format .`；可选 `pre-commit install` 自动执行。
- 注释和文档字符串使用中文。

## 测试

```bash
uv run pytest        # 测试目录 tests/，无需 MySQL/Redis
```

- `tests/conftest.py` 用 SQLite 内存库（`StaticPool`）通过 `app.dependency_overrides` 覆盖 `get_db`；每个测试后自动清空所有表（autouse fixture）。
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

`app/plugins/` 下每个模块是一个可选能力。新增插件步骤：新建模块 → 实现 `setup(app)` → 在 `setup_plugins`（`app/plugins/__init__.py`）中按开关登记 → 在 `Settings` 加 `ENABLE_XXX` 开关。现有插件：`request_id`（请求 ID + 日志 Filter；X-Request-ID 仅接受 `^[A-Za-z0-9_-]{1,64}$`，非法值自动生成 uuid）、`rate_limit`（slowapi，超限返回统一格式 429）、`metrics`（Prometheus `/metrics`；配置 `METRICS_TOKEN` 后要求 `Bearer <token>`，否则 401，未配置保持公开）、`cache`（`@cached("user", ttl=300)` 装饰器，Redis 未初始化时自动回退直调，无需开关）。

## 部署

```bash
docker build -t fastapi_app .
docker compose -f docker-compose.prod.yml up -d          # 需先在 .env 设置 SECRET_KEY 与 MYSQL_PASSWORD
docker compose -f docker-compose.prod.yml exec fastapi_app alembic upgrade head   # 首次部署执行迁移
```

Dockerfile：多阶段构建——`node:22-alpine` 阶段 `npm ci && npm run build` 产出前端 dist，`python:3.12-slim` + uv 阶段 `uv sync --frozen --no-dev` 并复制 dist（由后端托管）；非 root 用户运行，HEALTHCHECK 打 `/health`，启动命令 `fastapi run app/main.py`。

CI（GitHub Actions，push main 或 PR）：`uv sync --frozen` → `ruff check` + `ruff format --check` → `mypy app tests` → `pytest --cov=app --cov-fail-under=82` → 前端 `npm ci && npm run build` → `docker build`。依赖版本由 renovate 自动跟踪（`renovate.json`）。

## 安全注意事项

- **PROD 模式启动校验**：`MODE=PROD` 时未显式设置 `SECRET_KEY` 或 `MYSQL_PASSWORD` 会直接抛错拒绝启动——不要在生产环境绕过此校验。
- 密码一律用 `security.hash_password`（Argon2）入库；响应模型（`UserOut` 等）不得包含 `password` 字段。
- JWT 密钥、数据库密码只通过环境变量/`.env` 注入，`.env` 不得提交；`.env.example` 只放开发默认值。
- 全局异常处理器不会把内部异常细节返回给客户端（只进日志），新增异常处理时保持这一原则。
- 受保护接口通过 `Depends(get_current_user)` 鉴权；令牌无效、用户不存在或 `is_active=False` 均返回 401。
- 后台用户与会员（C 端）分表分令牌：JWT 带 `aud` claim（`admin` / `member`），后台接口用 `Depends(get_current_user)`，会员接口用 `Depends(get_current_member)`，两类令牌互不通用（跨用返回 401）。
