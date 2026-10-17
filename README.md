# fastapi_app

基于 FastAPI + Vue 3 的全栈脚手架项目：分层架构、JWT 认证、MySQL + Redis、Alembic 迁移、统一响应格式、全局异常处理、可插拔中间件能力，以及开箱即用的测试与 CI。

## 技术栈

- [FastAPI](https://fastapi.tiangolo.com/) + Pydantic v2 + SQLAlchemy 2.0
- [Vue 3](https://vuejs.org/) + Vite + TypeScript + Pinia + Vue Router + Element Plus + [VueUse](https://vueuse.org/)（`frontend/`）
- MySQL 8（SQLAlchemy 2.0 + asyncmy 异步驱动；Alembic 迁移走 PyMySQL 同步引擎）、Redis（`redis.asyncio`）
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
│   ├── api/                 # 路由层（自动发现注册，并统一加 /api/v1 前缀，新增文件即生效）
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
├── frontend/                # Vue 3 + Vite + TS 前端（自动导入 vue / vue-router / pinia / VueUse）
│   ├── src/api/             # axios 封装（自动带 token、解包统一响应、401 跳登录）
│   ├── src/composables/     # 项目 composable：主题 / 页面标题 / 连接状态（免 import）
│   ├── src/stores/          # Pinia（认证状态；令牌在 HttpOnly Cookie 中，前端不持有）
│   ├── src/router/          # 路由与登录守卫
│   ├── src/layouts/         # 后台布局（侧边栏、顶栏、暗色切换与离线指示）
│   ├── src/views/           # Login / Register / 概览 / 用户管理 / 会员管理 / 审计日志
│   └── src/auto-imports.d.ts  # 自动导入类型清单（需入库，供 vue-tsc 使用）
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
npm run dev        # http://localhost:5173，/api 前缀已代理到 127.0.0.1:8000
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
| POST | `/api/v1/auth/login` | 登录，返回 `TokenOut`（access + refresh 双令牌）并种下双 HttpOnly Cookie（账号不存在/密码错误/已禁用一律 401） |
| POST | `/api/v1/auth/refresh` | 刷新令牌（轮换制：旧 refresh 一次性使用，cookie 优先、body 兜底） |
| POST | `/api/v1/auth/logout` | 登出：access jti 进黑名单 + refresh jti 删除 + 清 Cookie |
| POST | `/api/v1/auth/change-password` | 修改当前登录用户密码（校验原密码，新密码沿用强度规则；成功后终止该账号全部会话） |
| POST | `/api/v1/member/register` | 会员注册（公开） |
| POST | `/api/v1/member/login` | 会员登录（同 `/api/v1/auth/login`，aud=member） |
| POST | `/api/v1/member/refresh` | 会员刷新令牌 |
| POST | `/api/v1/member/logout` | 会员登出并撤销令牌 |
| POST | `/api/v1/member/change-password` | 修改当前登录会员密码（同 `/api/v1/auth/change-password`） |
| POST | `/api/v1/member/verify-email` | 凭验证邮件链接中的 token 标记邮箱已验证（公开，token 即凭证） |
| POST | `/api/v1/member/resend-verification` | 重发验证邮件（member 令牌，已验证则返回无需重发） |
| GET | `/api/v1/member/me` | 获取当前登录会员（member 令牌） |
| PATCH | `/api/v1/member/me` | 更新当前会员昵称（member 令牌） |
| DELETE | `/api/v1/member/me` | 注销当前会员（软删除并撤销令牌） |
| GET | `/api/v1/member/` | 分页获取会员列表（admin 令牌） |
| GET | `/api/v1/user/{user_id}` | 通过用户 id 获取用户（不存在 404） |
| POST | `/api/v1/user/` | 创建用户；空库时首个管理员可自举（`ALLOW_BOOTSTRAP`，PROD 默认关闭），表非空需 admin 令牌；密码 Argon2 哈希入库 |
| GET | `/api/v1/user/me` | 获取当前登录用户（admin 令牌） |
| GET | `/api/v1/user/` | 分页获取用户列表（admin 令牌，`page` 1-10000、`page_size` 1-100 默认 20） |
| PATCH | `/api/v1/user/{user_id}` | 更新用户（admin，`{name?,is_active?}`，不存在 404）；置 `is_active=false` 会撤销该账号全部令牌 |
| DELETE | `/api/v1/user/{user_id}` | 软删除用户（admin，删自己 400）；同时撤销该账号全部令牌 |
| POST | `/api/v1/user/verify-email` | 验证后台账号邮箱 |
| POST | `/api/v1/user/resend-verification` | 重发后台账号验证邮件（admin 令牌） |
| GET | `/api/v1/audit/logs` | 分页查询审计日志（admin 令牌，可按 `action`/`actor_id` 过滤，按时间倒序） |
| GET | `/api/v1/tasks` | 异步任务观测：`ENABLE_TASKS` 开启后返回运行状态与最近任务（需 `task:read`；`?job_id=` 查单条，`?limit=` 条数 1-100） |
| POST | `/api/v1/tasks/dispatch` | 手动投递一个已注册的具名任务，返回 `job_id`（需 `task:write`） |
| GET | `/api/v1/tasks/history` | 任务执行历史分页查询（落库表 `task_run`，支持任务名/状态/时间区间过滤，需 `task:read`） |
| DELETE | `/api/v1/tasks/history` | 清理 `?days=` 天之前的历史记录（需 `task:write`） |
| GET | `/api/v1/scheduler` | 定时计划列表（cron、上次/下次触发时间、最近 job_id，需 `task:read`） |

所有业务接口统一挂在 `/api/v1` 版本前缀下（由 `register_routers` 自动添加，新增模块无需关心），
路径风格统一为 REST：集合资源用斜杠结尾形式（`GET /api/v1/user/`、`POST /api/v1/user/`），
单个资源用路径参数（`GET /api/v1/user/{user_id}`）。早期的 RPC 风格路径
（`/user/list`、`/user/get_user?user_id=`、`/user/create_user`、`/member/list`）已彻底移除，
不再保留兼容别名。认证与验证类动作仍用动词路径（`/api/v1/auth/login`、`/api/v1/member/verify-email`）。
`/health`、`/metrics`、`/docs` 为运维端点，不参与版本化，保留在根路径。

注册/创建用户的密码策略：8-64 位且同时包含字母和数字；name/nickname 2-32 位、去空白后非空；email 统一小写归一化。软删除不释放邮箱唯一约束，已删账号邮箱再注册返回 409。

邮箱验证：会员注册会自动投递验证邮件，`POST /api/v1/member/verify-email` 凭 token 完成验证。**默认不强制**（`REQUIRE_EMAIL_VERIFICATION=false`，未验证也能登录），置为 `true` 后未验证账号登录返回 403。脚手架不内置 SMTP，`app/services/mailer.py` 的默认实现只把验证链接写进日志，接入真实邮件服务时替换该函数即可。

审计日志：账号创建/更新/删除、改密、会员注销、登录失败都会写入 `audit_log`，用 `GET /api/v1/audit/logs` 查询。审计有自己的事务边界（写完立即提交），写入失败只记日志、不影响业务结果。

异步任务：`ENABLE_TASKS=true` 后后台「异步任务」页即可观测——运行指标（执行中/队列积压/队列深度/消费者）、最近任务列表（状态、耗时、错误信息，可按 `job_id` 回看单条）、执行历史（落库到 `task_run` 表，可按任务名/状态/时间区间分页回溯，重启与 TTL 到期都不丢）、`@scheduler.cron` 登记的定时计划，并可在页面上手动投递已注册的具名任务（需 `task:write`）。插件未启用时接口是 404，页面显示「未启用」空态而不是报错。详见下方「异步任务控制台」。

认证要点：access token 默认 30 分钟、refresh token 默认 7 天；后台（`aud=admin`）与会员（`aud=member`）令牌互不通用。access token 带 `type="access"`、refresh token 带 `type="refresh"`，**受保护接口只接受访问令牌，refresh 令牌直接当访问令牌用一律 401**（否则登出后凭长效 refresh 令牌仍可操作后台接口）。登出只让当前设备的令牌失效；**停用/软删用户、会员注销则撤销该账号全部令牌（含其他设备）**，重新启用也不会复活旧会话。取令牌顺序为 `Authorization: Bearer` 优先、其次 `access_token` Cookie；**Cookie 来源的写请求（POST/PUT/PATCH/DELETE）必须携带 `X-Requested-With: XMLHttpRequest`，否则 403**（Bearer 来源不受此约束）。

所有接口返回统一格式（路由声明了 `response_model`，Swagger 可见真实结构）：

```json
{"code": 200, "data": {}, "message": "请求成功"}
```

调用受保护接口（方式一：Bearer token）：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"zhangsan@test.com","password":"abcd1234"}' | jq -r .data.access_token)

curl http://127.0.0.1:8000/api/v1/user/me -H "Authorization: Bearer $TOKEN"
```

方式二：登录后由 Cookie 会话直接访问（写请求需带 CSRF 头）：

```bash
curl -c cookie.jar -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"zhangsan@test.com","password":"abcd1234"}'

curl -b cookie.jar http://127.0.0.1:8000/api/v1/user/me

curl -b cookie.jar -X POST http://127.0.0.1:8000/api/v1/user/ \
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
| `SECRET_KEY` | 开发默认值 | JWT 签名密钥，PROD 必须显式设置；**至少 32 字节**（HS256 安全下限，所有模式生效） |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | access token 有效期（分钟） |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | refresh token 有效期（天） |
| `COOKIE_SECURE` | 未设置（PROD 开、DEV 关） | 认证 Cookie 是否仅经 HTTPS 发送 |
| `METRICS_TOKEN` | 未设置（公开） | 配置后 `/metrics` 需 `Bearer <token>` 访问 |
| `JWT_ALGORITHM` | `HS256` | 仅允许 HS256/HS384/HS512；令牌含 `iat`/`exp`/`aud`（admin/member 隔离） |
| `ENABLE_DOCS` | 未设置（DEV 开、PROD 关） | API 文档开关，显式设置则以设置为准 |
| `CORS_ORIGINS` | `http://localhost,...` | 允许的跨域来源，逗号分隔 |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_SECONDS` | `5` / `900` | 同一账号连续登录失败达阈值后锁定的时长（秒）；计数存 Redis，Redis 不可用时 fail-open |
| `LOGIN_IP_MAX_FAILURES` | `0` | 来源 IP 维度的失败阈值；`0` 表示不启用（IP 层已有全局限流兜底） |
| `ENABLE_RATE_LIMIT` / `RATE_LIMIT` | `false` / `100/minute` | 限流插件 |
| `RATE_LIMIT_STORAGE_URI` | 未设置（自动推导 Redis） | 限流计数存储；设为 `memory://` 强制退回进程内存，见下方说明 |
| `ENABLE_METRICS` | `false` | Prometheus `/metrics` 插件 |
| `ENABLE_I18N` | `true` | 国际化插件：按请求解析语种并翻译统一响应文案，详见 `AGENTS.md` |
| `I18N_LOCALES` / `I18N_DEFAULT_LOCALE` | `zh-CN,en-US` / `zh-CN` | i18n 支持语种与默认语种 |
| `ENABLE_REQUEST_ID` | `true` | 请求 ID 中间件插件 |
| `ENABLE_TASKS` / `TASK_BACKEND` | `false` / `memory` | 异步任务插件开关与后端（memory 进程内 / redis 分布式） |
| `TASK_PERSIST_HISTORY` / `TASK_HISTORY_RETENTION_DAYS` | `true` / `30` | 任务执行历史落库开关与保留天数（关闭后只保留内存/Redis 中的最近记录） |
| `FRONTEND_DIST_DIR` | `frontend/dist` | 前端构建产物目录，存在时由后端托管为 SPA |

## 可插拔插件

`app/plugins/` 下每个模块是一个可选能力，实现 `setup(app)` 并在 `app/plugins/__init__.py` 按配置开关挂载。新增插件只需：新建模块 → 写 `setup(app)` → 在 `setup_plugins` 中登记 → 在 `Settings` 加开关。

缓存装饰器示例：

```python
from app.plugins.cache import cached


@cached("user", ttl=300)
async def heavy_query(user_id: int): ...
```

## 异步任务控制台

`ENABLE_TASKS=true`（可选 `ENABLE_SCHEDULER=true`）后，后台侧边栏出现「异步任务」页（`frontend/src/views/TaskManageView.vue`），把两个插件的观测接口拼成一张运维面板：

| 区块 | 数据来源 | 说明 |
| --- | --- | --- |
| 运行指标 | `GET /api/v1/tasks` | 后端类型、`执行中`、`队列积压/待处理`、`队列深度/已跟踪`、`Worker/消费者`；memory 与 redis 两种后端展示各自有意义的指标 |
| 定时计划 | `GET /api/v1/scheduler` | `ENABLE_SCHEDULER` 开启时展示 cron、时区、上次/下次触发时间、最近一次投递的 job_id |
| 最近任务 | `GET /api/v1/tasks` | 任务名、状态、重试次数、入队时间、耗时；详情抽屉里看错误与返回值；可按 `job_id` 回看单条（redis 后端无全局列表时的定位入口） |
| 执行历史 | `GET /api/v1/tasks/history` | 落库记录的分页回溯：任务名/状态/时间区间过滤，按入队时间倒序；详情抽屉与「最近任务」共用，看错误与返回值 |
| 手动投递 | `POST /api/v1/tasks/dispatch` | 选一个已注册任务名（`registered` 列表）+ JSON 参数投递，便于验证链路（需 `task:write`） |

要点：

- **权限**：观测需 `task:read`、投递与清理需 `task:write`（种子内置，`admin` 两者都有，`viewer` 都没有）；菜单与按钮按同一套权限码控制。
- **插件未启用**：接口不存在（404），页面渲染「未启用」空态并提示开启方式，不弹错误提示；概览页的异步任务卡片也会自动隐藏。
- **memory 后端**：「最近任务」只在进程内存（最近 1000 条），重启即丢；刷新页面看到的是当前进程的任务。
- **redis 后端**：Web 进程只投递/查询，**消费由 `python -m app.plugins.tasks_worker` 独立进程完成**；投递时额外把 job_id 写入有上限的 `task:recent` 索引，页面才能列出「最近任务」。没起 worker 时会看到「待处理」只增不减。
- **执行历史落库**：两种后端都会在「入队 / 开始执行 / 成功 / 失败 / 重试」节点把一次运行的最终状态写入 `task_run` 表（按 `job_id` 覆盖式更新，同一任务只留一条最新记录，返回值截断 500 字）。写库是**旁路**：失败只记 `warning` 日志，绝不打断任务执行。保留策略有两种——页面上按天数清理（`DELETE /api/v1/tasks/history?days=N`，需 `task:write`），或启用示例定时任务 `cleanup_task_history`（`app/tasks.py`，每天 04:00 按 `TASK_HISTORY_RETENTION_DAYS` 清理）。设 `TASK_PERSIST_HISTORY=false` 可完全关闭落库。

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
uv run pytest            # 测试（默认 SQLite 内存库，无需 MySQL/Redis）
docker compose up -d     # 启动 MySQL + Redis
```

测试默认跑在 SQLite 内存库上。设置 `TEST_DATABASE_URL` 可让同一套用例跑在真实
MySQL 上，覆盖 SQLite 测不到的方言差异（CI 中两个都跑）：

```bash
TEST_DATABASE_URL=mysql+asyncmy://root:123456@127.0.0.1:3306/fastapi_test uv run pytest
```

校验模型与迁移是否漂移（需先连上数据库并 `alembic upgrade head`）：

```bash
uv run alembic check     # 有差异时非零退出
```

可选安装 pre-commit 钩子：`pre-commit install`

## 部署

> **必须先有 HTTPS 终止层。** PROD 模式下认证 Cookie 带 `Secure` 标记，而
> `docker-compose.prod.yml` 只暴露 8000 端口、不提供 TLS。直接把容器暴露到公网
> 用 http 访问时，浏览器会拒绝保存认证 Cookie——表现为登录接口返回 200、随后
> 所有请求 401。请在前面挂 Nginx / Caddy / 云负载均衡做 TLS 终止，再把 8000
> 端口反代到域名后访问（同源部署时前后端共用域名，无需额外配置 CORS）。

```bash
# 构建镜像（多阶段构建：前端 npm run build + 后端依赖，单容器同时托管前后端）
docker build -t fastapi_app .

# 启动完整环境（需先在 .env 中设置 SECRET_KEY 与 MYSQL_PASSWORD）
docker compose -f docker-compose.prod.yml up -d
```

### 初始化首个管理员

自举与「创建后台用户」是两个端点，语义分离：

| 端点 | 用途 | 鉴权 |
| --- | --- | --- |
| `POST /api/v1/auth/bootstrap` | 空库时创建首个管理员（自动授予超级管理员） | 免鉴权，但受 `ALLOW_BOOTSTRAP` 约束 |
| `POST /api/v1/user/` | 日常创建后台账号 | 恒需 admin 令牌 + `user:write` |

前端登录页会读 `GET /api/v1/info` 的 `needs_bootstrap` 决定「创建首个管理员」
入口是否展示。PROD 下 `ALLOW_BOOTSTRAP` 默认关闭，请临时开启→初始化→再关闭，
或用 `scripts/seed_rbac.py` 一类脚本建号。

### RBAC 种子数据的增量补齐

角色与权限的播种只在「自举」路径触发，新增权限点（如 `audit:read`）不会自动
进入存量库。升级版本后执行一次即可（幂等，可重复运行）：

```bash
uv run python scripts/seed_rbac.py
```

它会补齐缺失的权限点与内置角色的权限关联，并清空用户权限缓存（否则最长
300 秒仍是旧权限）。

### 健康检查

| 端点 | 语义 | 建议用途 |
| --- | --- | --- |
| `/health/live` | 只判断进程存活，不探依赖 | 容器 HEALTHCHECK / k8s liveness |
| `/health/ready` 与 `/health` | 探 MySQL + Redis，任一不可用返回 503 | 网关摘流量 / k8s readiness |

用 `/health` 做容器探活会把 Redis 的一次抖动放大成容器重启，务必区分。

**数据库迁移已内建到容器启动命令**：容器 CMD 会先执行 `alembic upgrade head`
再拉起服务，因此无需（也不应）手动进容器跑迁移；迁移失败时容器直接退出，
不会带着不匹配的 schema 对外提供服务。如需单独执行：

```bash
docker compose -f docker-compose.prod.yml exec fastapi_app alembic upgrade head
```

worker 数默认为 2，可按容器 CPU 调整：

```yaml
# docker-compose.prod.yml → fastapi_app.environment
WEB_CONCURRENCY: "4"
```

**限流计数默认已是全局共享的。** `RATE_LIMIT_STORAGE_URI` 不设置时，会按
`REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` 自动拼出连接串（密码的特殊字符
自动做 URL 编码，不会拼出非法 URI）。这样 N 个 worker 共用一份计数，实际阈值
就等于 `RATE_LIMIT`，不随 worker 数放大。

只有两种情况下会退回进程内存计数（此时阈值放大为 `RATE_LIMIT × N`，实测
2 worker + `100/minute` 约 `200/minute`）：

```yaml
# 1. 显式指定（单机调试、测试用）
RATE_LIMIT_STORAGE_URI: "memory://"
# 2. Redis 不可用：各 worker 自动降级并打 error 日志，存储恢复后需重启进程
```

降级优于直接放行：Redis 故障期间若沿用 fail-open，限流会整体消失；退回内存
至少还保留一层保护。降级过程中放行的那一次请求无法补记，属可接受误差。

生产环境建议确认以下开关（compose 已内置，本地自管部署需自行设置）：

| 项 | 生产建议值 | 说明 |
| --- | --- | --- |
| `MODE` | `PROD` | 大小写不敏感；非 PROD 会套用 DEV 策略（文档开放、Cookie 不带 Secure、跳过密钥强校验） |
| `ALLOW_BOOTSTRAP` | `false` | 关闭后空库也不能免鉴权建号；首个管理员请用脚本/CLI 创建 |
自管部署还需注意：MySQL 8 默认 `caching_sha2_password`，服务端认证缓存为空时
（首次连接、MySQL 重启或主从切换后）异步驱动 asyncmy 必须依赖 `cryptography`
完成 RSA 握手，缺包会表现为「重启 MySQL 后服务连不上库」。该依赖已在
`pyproject.toml` 中声明，不要从生产镜像里裁掉。

| `ENABLE_RATE_LIMIT` | `true` | 登录/注册接口无本地防爆破，全局限流是唯一兜底 |
| `RATE_LIMIT_STORAGE_URI` | 留空（自动推导 Redis） | 多 worker 下显式设 `memory://` 会让阈值 = `RATE_LIMIT × worker 数` |
| `ENABLE_METRICS` | `true` | 不开启时 `/metrics` 为 404，生产建议配合 `METRICS_TOKEN` 打开 |
| `METRICS_TOKEN` | 随机串 | 不配置时 `/metrics` 公开 |
