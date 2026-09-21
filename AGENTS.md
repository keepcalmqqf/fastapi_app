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
│   │   ├── deps.py          # get_current_user（HTTPBearer）依赖
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
│   ├── src/views/           # Login / Register / Home 页面
│   └── vite.config.ts       # dev proxy：/auth /user /member /health → 127.0.0.1:8000
├── alembic/                 # 数据库迁移（env.py 从 Settings 读连接串）
├── tests/                   # pytest（conftest 用 SQLite 内存库覆盖 get_db）
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
| `REDIS_HOST`/`REDIS_PORT` | 127.0.0.1/6379 | PROD 下默认 `redis` |
| `SECRET_KEY` / `ACCESS_TOKEN_EXPIRE_MINUTES` / `JWT_ALGORITHM` | 开发默认值 / 1440 / HS256 | JWT 签名配置 |
| `CORS_ORIGINS` | `http://localhost,http://localhost:8080` | 逗号分隔 |
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

启动后：Swagger UI 在 `http://127.0.0.1:8000/docs`，健康检查在 `/health`（返回 mysql/redis 连通性）。

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
- 测试通过 `TestClient`（`client` fixture）走完整 HTTP 链路，覆盖认证、校验错误、统一响应格式等。
- 新增功能应在 `tests/` 下补充对应 API 测试，断言统一响应的 `code`/`data` 字段。

## 数据库迁移

模型是 schema 的唯一来源，不要手写 SQL 建表：

```bash
uv run alembic revision --autogenerate -m "add xxx"
uv run alembic upgrade head
```

`alembic/env.py` 从 `Settings.SQLALCHEMY_DATABASE_URL` 读连接串，迁移环境与运行配置自动一致。

## 插件开发约定

`app/plugins/` 下每个模块是一个可选能力。新增插件步骤：新建模块 → 实现 `setup(app)` → 在 `setup_plugins`（`app/plugins/__init__.py`）中按开关登记 → 在 `Settings` 加 `ENABLE_XXX` 开关。现有插件：`request_id`（请求 ID + 日志 Filter）、`rate_limit`（slowapi，超限返回统一格式 429）、`metrics`（Prometheus `/metrics`）、`cache`（`@cached("user", ttl=300)` 装饰器，Redis 未初始化时自动回退直调，无需开关）。

## 部署

```bash
docker build -t fastapi_app .
docker compose -f docker-compose.prod.yml up -d          # 需先在 .env 设置 SECRET_KEY 与 MYSQL_PASSWORD
docker compose -f docker-compose.prod.yml exec fastapi_app alembic upgrade head   # 首次部署执行迁移
```

Dockerfile：多阶段构建——`node:22-alpine` 阶段 `npm ci && npm run build` 产出前端 dist，`python:3.12-slim` + uv 阶段 `uv sync --frozen --no-dev` 并复制 dist（由后端托管）；非 root 用户运行，HEALTHCHECK 打 `/health`，启动命令 `fastapi run app/main.py`。

CI（GitHub Actions，push main 或 PR）：`uv sync --frozen` → `ruff check` → `pytest` → 前端 `npm ci && npm run build` → `docker build`。依赖版本由 renovate 自动跟踪（`renovate.json`）。

## 安全注意事项

- **PROD 模式启动校验**：`MODE=PROD` 时未显式设置 `SECRET_KEY` 或 `MYSQL_PASSWORD` 会直接抛错拒绝启动——不要在生产环境绕过此校验。
- 密码一律用 `security.hash_password`（Argon2）入库；响应模型（`UserOut` 等）不得包含 `password` 字段。
- JWT 密钥、数据库密码只通过环境变量/`.env` 注入，`.env` 不得提交；`.env.example` 只放开发默认值。
- 全局异常处理器不会把内部异常细节返回给客户端（只进日志），新增异常处理时保持这一原则。
- 受保护接口通过 `Depends(get_current_user)` 鉴权；令牌无效、用户不存在或 `is_active=False` 均返回 401。
- 后台用户与会员（C 端）分表分令牌：JWT 带 `aud` claim（`admin` / `member`），后台接口用 `Depends(get_current_user)`，会员接口用 `Depends(get_current_member)`，两类令牌互不通用（跨用返回 401）。
