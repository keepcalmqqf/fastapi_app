# fastapi_app

基于 FastAPI 的脚手架项目，包含分层架构、MySQL + Redis、统一响应格式与全局异常处理。

## 技术栈

- [FastAPI](https://fastapi.tiangolo.com/) + Pydantic v2 + SQLAlchemy 2.0
- MySQL 8（SQLAlchemy + PyMySQL）
- Redis（`redis.asyncio`）
- [uv](https://docs.astral.sh/uv/) 包管理
- Docker / Docker Compose 部署

## 目录结构

```
├── main.py                  # 应用入口：lifespan、中间件、异常处理、路由注册
├── config/
│   ├── main.py              # 配置项（pydantic-settings，支持环境变量覆盖）
│   ├── database.py          # SQLAlchemy 引擎与 get_db 依赖
│   ├── models.py            # ORM 模型
│   ├── redis.py             # Redis 客户端工厂
│   ├── middleware.py        # CORS 中间件
│   ├── exception_handler.py # 全局异常处理（统一响应格式）
│   ├── create_models.py     # 通过 sqlacodegen 从数据库反向生成模型
│   └── fastapi_db.sql       # 数据库初始化脚本
├── controller/              # 路由层
├── service/                 # 业务逻辑层
├── crud/                    # 数据访问层
├── domain/                  # Pydantic 请求/响应模型
└── util/result.py           # 统一响应格式 ok() / failure()
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

首次启动 MySQL 会自动执行 `config/fastapi_db.sql` 完成建表。

### 3. 安装依赖

需要安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后：

```bash
uv sync
```

### 4. 运行项目

```bash
uv run fastapi dev
```

启动后访问：

- 接口文档（Swagger UI）：http://127.0.0.1:8000/docs
- 接口文档（ReDoc）：http://127.0.0.1:8000/redoc

## 接口列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/user/get_user?user_id=1` | 通过用户 id 获取用户 |
| POST | `/user/create_user` | 创建用户 |

创建用户请求体示例：

```json
{
  "name": "张三",
  "email": "zhangsan@test.com",
  "password": "123456",
  "is_active": true
}
```

所有接口返回统一格式：

```json
{"code": 200, "data": {}, "message": "请求成功"}
```

## 配置说明

配置由 `config/main.py` 的 `Settings` 管理，默认值面向本地开发，均可通过环境变量（或 `.env` 文件）覆盖：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MODE` | `DEV` | `PROD` 时 MySQL/Redis 主机自动切换为 docker 服务名 |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL 地址 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `123456` | MySQL 账号 |
| `MYSQL_DATABASE` | `fastapi_db` | 数据库名 |
| `REDIS_HOST` / `REDIS_PORT` | `127.0.0.1` / `6379` | Redis 地址 |

## 错误处理

全局异常处理（`config/exception_handler.py`）保证任何错误都返回统一 JSON 格式：

- 参数校验失败 → `code: 422`，返回具体校验错误信息
- Redis / 数据库连接失败 → `code: 500`，提示具体是哪个服务异常
- 其他未处理异常 → `code: 500`，完整堆栈输出到服务端日志

## 部署

### 构建镜像

```bash
docker build -t fastapi_app .
```

### 启动完整环境（含应用）

```bash
docker compose -f docker-compose.prod.yml up -d
```

## 常用命令

```bash
uv sync                  # 安装/同步依赖
uv add <package>         # 添加依赖
uv run fastapi dev       # 开发模式运行（自动重载）
uv run fastapi run main.py  # 生产模式运行
docker compose up -d     # 启动 MySQL + Redis
docker compose down      # 停止依赖服务
```
