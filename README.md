# fastapi_app

基于 FastAPI + Vue 3 的全栈脚手架项目：分层架构、JWT 认证、MySQL + Redis、Alembic 迁移、统一响应格式、全局异常处理、可插拔中间件能力，以及开箱即用的测试与 CI。

## 技术栈

- [FastAPI](https://fastapi.tiangolo.com/) + Pydantic v2 + SQLAlchemy 2.0
- [Vue 3](https://vuejs.org/) + Vite + TypeScript + Pinia + Vue Router + Element Plus（`frontend/`）
- MySQL 8（SQLAlchemy + PyMySQL）、Redis（`redis.asyncio`）
- JWT 认证（PyJWT）+ Argon2 密码哈希（pwdlib）
- Alembic 数据库迁移
- [uv](https://docs.astral.sh/uv/) 包管理，ruff + mypy + pytest + pre-commit
- Docker / Docker Compose 部署（单容器同时托管前端产物与后端 API）

## 目录结构

```
├── app/
│   ├── main.py              # 应用入口：lifespan、中间件、异常处理、插件、路由注册
│   ├── core/                # 核心设施
│   │   ├── settings.py      # 配置项（pydantic-settings，环境变量覆盖，PROD 强制密钥）
│   │   ├── database.py      # SQLAlchemy 引擎与 get_db 依赖
│   │   ├── redis.py         # Redis 客户端工厂
│   │   ├── security.py      # 密码哈希、JWT 签发/校验
│   │   ├── deps.py          # get_current_user/get_current_member（async 依赖，黑名单 + CSRF 校验）
│   │   ├── result.py        # 统一响应 Result[T] 泛型
│   │   ├── middleware.py    # CORS 中间件（origins 走配置）
│   │   └── exceptions.py    # 全局异常处理（统一响应，内部细节只进日志）
│   ├── api/                 # 路由层（自动发现注册，新增文件即生效）
│   ├── services/            # 业务逻辑层（事务边界在此）
│   ├── repositories/        # 数据访问层（不 commit）
│   ├── models/              # SQLAlchemy ORM 模型
│   ├── schemas/             # Pydantic 请求/响应模型
│   └── plugins/             # 可插拔能力，按配置开关挂载
│       ├── __init__.py      # 插件注册入口 setup_plugins(app)
│       ├── request_id.py    # 请求 ID 中间件 + 日志串联
│       ├── rate_limit.py    # 限流（slowapi）
│       ├── metrics.py       # Prometheus 指标（/metrics）
│       └── cache.py         # Redis 缓存装饰器 @cached
├── frontend/                # Vue 3 + Vite + TS 前端（登录/注册/主页）
│   ├── src/api/             # axios 封装（自动带 token、解包统一响应、401 跳登录）
│   ├── src/stores/          # Pinia（认证状态，token 持久化）
│   ├── src/router/          # 路由与登录守卫
│   └── src/views/           # 页面（Login / Register / Home）
├── alembic/                 # 数据库迁移
└── tests/                   # pytest 测试（SQLite 内存库）
```

## 快速开始

### 1. 拉取代码

```bash
git clone https://github.com/qiuqfang/fastapi_app.git
cd fastapi_app
```

### 2. 启动依赖服务（MySQL + Redis）

```bash
docker compose up -d
```

### 3. 安装依赖并初始化数据库

需要安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后：

```bash
uv sync
uv run alembic upgrade head   # 建表（schema 由 Alembic 管理，勿再用 sql 文件）
```

### 4. 运行项目

```bash
uv run fastapi dev
```

启动后访问：

- 接口文档（Swagger UI）：http://127.0.0.1:8000/docs（`ENABLE_DOCS` 未设置时 DEV 开、PROD 关）
- 健康检查：http://127.0.0.1:8000/health（组件全部正常 200 + `status=ok`；任一组件 down 503 + `status=degraded`，body 均为统一 Result 格式）

### 5. 运行前端（开发模式）

需要 Node.js ≥ 18：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/auth /user /health 已代理到 127.0.0.1:8000
```

前端包含登录、注册和主页（展示当前登录用户）。开发期通过 Vite proxy 调后端，无需处理跨域。

### 6. 前端生产构建（由后端托管）

```bash
cd frontend && npm run build   # 产出 frontend/dist
```

`frontend/dist` 存在时，FastAPI 启动后自动将其托管为 SPA（未命中的路径回退 `index.html`，API 路由优先匹配不受影响），访问 `http://127.0.0.1:8000/` 即是前端页面。托管目录可通过环境变量 `FRONTEND_DIST_DIR` 覆盖。

## 接口列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（MySQL + Redis 连通性，组件 down 时 503） |
| POST | `/auth/login` | 登录，返回 `TokenOut`（access + refresh 双令牌）并种下双 HttpOnly Cookie（账号不存在/密码错误/已禁用一律 401） |
| POST | `/auth/refresh` | 刷新令牌（轮换制：旧 refresh 一次性使用，cookie 优先、body 兜底） |
| POST | `/auth/logout` | 登出：access jti 进黑名单 + refresh jti 删除 + 清 Cookie |
| POST | `/member/register` | 会员注册（公开） |
| POST | `/member/login` | 会员登录（同 `/auth/login`，aud=member） |
| POST | `/member/refresh` | 会员刷新令牌 |
| POST | `/member/logout` | 会员登出并撤销令牌 |
| GET | `/member/me` | 获取当前登录会员（member 令牌） |
| PATCH | `/member/me` | 更新当前会员昵称（member 令牌） |
| DELETE | `/member/me` | 注销当前会员（软删除并撤销令牌） |
| GET | `/member/list` | 分页获取会员列表（admin 令牌） |
| GET | `/user/get_user?user_id=1` | 通过用户 id 获取用户（不存在 404） |
| POST | `/user/create_user` | 创建用户；空库时首个管理员可无 token 自举，表非空需 admin 令牌；密码 Argon2 哈希入库 |
| GET | `/user/me` | 获取当前登录用户（admin 令牌） |
| GET | `/user/list` | 分页获取用户列表（admin 令牌，`page≥1`、`page_size` 1-100 默认 20） |
| PATCH | `/user/{user_id}` | 更新用户（admin，`{name?,is_active?}`，不存在 404） |
| DELETE | `/user/{user_id}` | 软删除用户（admin，删自己 400） |

注册/创建用户的密码策略：8-64 位且同时包含字母和数字；name/nickname 2-32 位、去空白后非空；email 统一小写归一化。软删除不释放邮箱唯一约束，已删账号邮箱再注册返回 409。

认证要点：access token 默认 30 分钟、refresh token 默认 7 天；后台（`aud=admin`）与会员（`aud=member`）令牌互不通用。取令牌顺序为 `Authorization: Bearer` 优先、其次 `access_token` Cookie；**Cookie 来源的写请求（POST/PUT/PATCH/DELETE）必须携带 `X-Requested-With: XMLHttpRequest`，否则 403**（Bearer 来源不受此约束）。

所有接口返回统一格式（路由声明了 `response_model`，Swagger 可见真实结构）：

```json
{"code": 200, "data": {}, "message": "请求成功"}
```

调用受保护接口（方式一：Bearer token）：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"zhangsan@test.com","password":"abcd1234"}' | jq -r .data.access_token)

curl http://127.0.0.1:8000/user/me -H "Authorization: Bearer $TOKEN"
```

方式二：登录后由 Cookie 会话直接访问（写请求需带 CSRF 头）：

```bash
curl -c cookie.jar -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"zhangsan@test.com","password":"abcd1234"}'

curl -b cookie.jar http://127.0.0.1:8000/user/me

curl -b cookie.jar -X POST http://127.0.0.1:8000/user/create_user \
  -H 'Content-Type: application/json' \
  -H 'X-Requested-With: XMLHttpRequest' \
  -d '{"name":"李四","email":"lisi@test.com","password":"abcd1234"}'
```

## 配置说明

配置由 `app/core/settings.py` 的 `Settings` 管理，均可通过环境变量（或 `.env` 文件，参考 `.env.example`）覆盖：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MODE` | `DEV` | `PROD` 时强制要求设置 `SECRET_KEY` 和 `MYSQL_PASSWORD` |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL 地址（PROD 默认 docker 服务名 `mysql`） |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `123456` | MySQL 账号 |
| `MYSQL_DATABASE` | `fastapi_db` | 数据库名 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | `127.0.0.1` / `6379` / 无 | Redis 地址（PROD 默认 `redis`） |
| `SECRET_KEY` | 开发默认值 | JWT 签名密钥，PROD 必须显式设置 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | access token 有效期（分钟） |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | refresh token 有效期（天） |
| `COOKIE_SECURE` | 未设置（PROD 开、DEV 关） | 认证 Cookie 是否仅经 HTTPS 发送 |
| `METRICS_TOKEN` | 未设置（公开） | 配置后 `/metrics` 需 `Bearer <token>` 访问 |
| `JWT_ALGORITHM` | `HS256` | 仅允许 HS256/HS384/HS512；令牌含 `iat`/`exp`/`aud`（admin/member 隔离） |
| `ENABLE_DOCS` | 未设置（DEV 开、PROD 关） | API 文档开关，显式设置则以设置为准 |
| `CORS_ORIGINS` | `http://localhost,...` | 允许的跨域来源，逗号分隔 |
| `ENABLE_RATE_LIMIT` / `RATE_LIMIT` | `false` / `100/minute` | 限流插件 |
| `ENABLE_METRICS` | `false` | Prometheus `/metrics` 插件 |
| `ENABLE_REQUEST_ID` | `true` | 请求 ID 中间件插件 |
| `FRONTEND_DIST_DIR` | `frontend/dist` | 前端构建产物目录，存在时由后端托管为 SPA |

## 可插拔插件

`app/plugins/` 下每个模块是一个可选能力，实现 `setup(app)` 并在 `app/plugins/__init__.py` 按配置开关挂载。新增插件只需：新建模块 → 写 `setup(app)` → 在 `setup_plugins` 中登记 → 在 `Settings` 加开关。

缓存装饰器示例：

```python
from app.plugins.cache import cached


@cached("user", ttl=300)
async def heavy_query(user_id: int): ...
```

## 数据库迁移

模型（`app/models/`）是 schema 的唯一来源：

```bash
uv run alembic revision --autogenerate -m "add xxx"   # 生成迁移
uv run alembic upgrade head                            # 应用迁移
```

## 开发

```bash
uv sync                  # 安装/同步依赖
uv add <package>         # 添加依赖
uv run fastapi dev       # 开发模式运行（自动重载）
uv run ruff check .      # lint
uv run ruff format .     # 格式化
uv run pytest            # 测试（SQLite 内存库，无需 MySQL/Redis）
docker compose up -d     # 启动 MySQL + Redis
```

可选安装 pre-commit 钩子：`pre-commit install`

## 部署

```bash
# 构建镜像（多阶段构建：前端 npm run build + 后端依赖，单容器同时托管前后端）
docker build -t fastapi_app .

# 启动完整环境（需先在 .env 中设置 SECRET_KEY 与 MYSQL_PASSWORD）
docker compose -f docker-compose.prod.yml up -d

# 首次部署执行迁移
docker compose -f docker-compose.prod.yml exec fastapi_app alembic upgrade head
```
