# fastapi_app

基于 FastAPI 的脚手架项目：分层架构、JWT 认证、MySQL + Redis、Alembic 迁移、统一响应格式、全局异常处理、可插拔中间件能力，以及开箱即用的测试与 CI。

## 技术栈

- [FastAPI](https://fastapi.tiangolo.com/) + Pydantic v2 + SQLAlchemy 2.0
- MySQL 8（SQLAlchemy + PyMySQL）、Redis（`redis.asyncio`）
- JWT 认证（PyJWT）+ Argon2 密码哈希（pwdlib）
- Alembic 数据库迁移
- [uv](https://docs.astral.sh/uv/) 包管理，ruff + mypy + pytest + pre-commit
- Docker / Docker Compose 部署

## 目录结构

```
├── app/
│   ├── main.py              # 应用入口：lifespan、中间件、异常处理、插件、路由注册
│   ├── core/                # 核心设施
│   │   ├── settings.py      # 配置项（pydantic-settings，环境变量覆盖，PROD 强制密钥）
│   │   ├── database.py      # SQLAlchemy 引擎与 get_db 依赖
│   │   ├── redis.py         # Redis 客户端工厂
│   │   ├── security.py      # 密码哈希、JWT 签发/校验
│   │   ├── deps.py          # get_current_user 依赖
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

- 接口文档（Swagger UI）：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## 接口列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（MySQL + Redis 连通性） |
| POST | `/auth/login` | 登录，返回 JWT |
| GET | `/user/get_user?user_id=1` | 通过用户 id 获取用户 |
| POST | `/user/create_user` | 创建用户（密码 Argon2 哈希入库） |
| GET | `/user/me` | 获取当前登录用户（需 Bearer token） |

所有接口返回统一格式（路由声明了 `response_model`，Swagger 可见真实结构）：

```json
{"code": 200, "data": {}, "message": "请求成功"}
```

调用受保护接口：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"zhangsan@test.com","password":"123456"}' | jq -r .data.access_token)

curl http://127.0.0.1:8000/user/me -H "Authorization: Bearer $TOKEN"
```

## 配置说明

配置由 `app/core/settings.py` 的 `Settings` 管理，均可通过环境变量（或 `.env` 文件，参考 `.env.example`）覆盖：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MODE` | `DEV` | `PROD` 时强制要求设置 `SECRET_KEY` 和 `MYSQL_PASSWORD` |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL 地址（PROD 默认 docker 服务名 `mysql`） |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `123456` | MySQL 账号 |
| `MYSQL_DATABASE` | `fastapi_db` | 数据库名 |
| `REDIS_HOST` / `REDIS_PORT` | `127.0.0.1` / `6379` | Redis 地址（PROD 默认 `redis`） |
| `SECRET_KEY` | 开发默认值 | JWT 签名密钥，PROD 必须显式设置 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | 令牌有效期 |
| `CORS_ORIGINS` | `http://localhost,...` | 允许的跨域来源，逗号分隔 |
| `ENABLE_RATE_LIMIT` / `RATE_LIMIT` | `false` / `100/minute` | 限流插件 |
| `ENABLE_METRICS` | `false` | Prometheus `/metrics` 插件 |
| `ENABLE_REQUEST_ID` | `true` | 请求 ID 中间件插件 |

## 可插拔插件

`app/plugins/` 下每个模块是一个可选能力，实现 `setup(app)` 并在 `app/plugins/__init__.py` 按配置开关挂载。新增插件只需：新建模块 → 写 `setup(app)` → 在 `setup_plugins` 中登记 → 在 `Settings` 加开关。

缓存装饰器示例：

```python
from app.plugins.cache import cached

@cached("user", ttl=300)
async def heavy_query(user_id: int):
    ...
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
# 构建镜像
docker build -t fastapi_app .

# 启动完整环境（需先在 .env 中设置 SECRET_KEY 与 MYSQL_PASSWORD）
docker compose -f docker-compose.prod.yml up -d

# 首次部署执行迁移
docker compose -f docker-compose.prod.yml exec fastapi_app alembic upgrade head
```
