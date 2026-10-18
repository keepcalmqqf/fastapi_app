# fastapi_app 代码问题分析报告

> 审查时间：2026-10-17 · 基线：`264 passed`，覆盖率 90%（`.venv` + SQLite）
> 审查范围：`app/` 全量 Python、`frontend/src`、 `tests/`、Docker 与 CI 配置
> 说明：每条均给出 `文件:行号` 证据，已逐条回源码核实。未列入纯风格类问题。

---

## 修复状态（2026-10-18）

P0 ~ P4 全部条目已修复，P3 的假绿灯用例全部改写为真实断言并补了回归测试。

| 指标 | 修复前 | 修复后 |
|---|---|---|
| pytest | 264 passed | **290 passed**（覆盖率 90%） |
| `ruff check` | 7 处（含 `app/main.py` 里未定义的 `logger`） | 通过 |
| `ruff format --check` | 7 个文件未格式化 | 通过 |
| `mypy app tests` | 39 errors | **31 errors** |
| `npm run build` | 通过 | 通过 |

新增测试 26 条，重点覆盖此前「改错了也不会红」的路径：bootstrap 整体回滚、刷新令牌
`GETDEL` 原子性、登录计数 `INCR`+`EXPIRE` 同批下发、WS 的 `aud` 与主体撤销、缓存键
稳定性、限流经 `setup_plugins` 的真实接线、CSRF 对 PATCH/DELETE、调度循环唤醒不被吞。

顺带修掉三个审查时未列出的缺陷（均由 `ruff` 或 `mypy` 暴露）：

1. `app/main.py` 的 lifespan 清理分支引用了**未定义的 `logger`**——只在「清理步骤抛异常」
   时才走到，测试覆盖不到，属真实的潜在 NameError。
2. `app/plugins/tasks.py::_recent_from_redis` 的 `zip()` 未指定 `strict=`，ids 与结果
   长度不一致时会静默截断。
3. `[tool.ruff.format]` 未排除 `alembic/versions/`，CI 的 `ruff format --check` 在既有
   迁移文件上就是红的（已按 mypy 的排除口径对齐，见 AGENTS.md「代码风格」）。

**未处理**：P3-7（SQLite 与 MySQL 方言差异、CI 的 MySQL job 未设 `--cov-fail-under`）
属测试基建改造，涉及 CI 编排，未在本次改动范围内。P4 的登出端点免鉴权经评估保持
现状（`SameSite=Lax` 已使其无法对他人生效，加鉴权反而让「已过期会话无法登出」）。

---

## 一、阻断级（P0）：生产环境直接不可用 / 可被接管

### P0-1 `docker-compose.prod.yml` 的 `$$` 转义把密码变成字面量，生产栈永远起不来

`docker-compose.prod.yml:15`、`:31`、`:37`

```yaml
15: test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "-uroot", "-p$${MYSQL_ROOT_PASSWORD}"]
31: command: ["redis-server", "--requirepass", "$${REDIS_PASSWORD}", "--appendonly", "yes"]
37: test: ["CMD", "redis-cli", "--no-auth-warning", "-a", "$${REDIS_PASSWORD}", "ping"]
```

**已实测**（`docker compose -f docker-compose.prod.yml config`）：

```
healthcheck.test: [..., "-p$${MYSQL_ROOT_PASSWORD}"]     # 运行时是字面量 ${MYSQL_ROOT_PASSWORD}
redis.command:    [..., "--requirepass", "$${REDIS_PASSWORD}", ...]
```

`$$` 只在宿主解析期阻止替换，运行期还原成字面 `$`；而这三处都是 **exec 数组形式**（容器内无 shell），`${...}` 不会被二次展开，最终 argv 就是一串字面量。

三条独立后果：

1. **MySQL 健康检查必然失败** → `retries:10` 后 unhealthy → `depends_on: condition: service_healthy`（`:75-79`）→ **`fastapi_app` 容器永远不会被启动**，整套生产编排不可用。
2. **Redis 密码是字面量 `${REDIS_PASSWORD}`**，应用拿的是 `.env` 真值（`:56`）→ AUTH 被拒，refresh jti / 黑名单 / 限流计数 / 调度锁全部失效。
3. **故障被掩盖**：37 行的 redis 健康检查用同一个错误字面量，恰好与 31 行匹配 → Redis 显示 healthy，运维只会看到 MySQL 卡在 starting。

### P0-2 PROD 强校验被空串绕过：漏配 `MYSQL_PASSWORD` → 生产库 root 空密码

`app/core/settings.py:228-232`

```python
if self.SECRET_KEY == _DEV_SECRET_KEY:
    raise ValueError("PROD 模式必须通过环境变量设置 SECRET_KEY")
if self.MYSQL_PASSWORD == "123456":  # ← 等值比较
    raise ValueError("PROD 模式必须通过环境变量设置 MYSQL_PASSWORD")
```

compose 对未设置的 `${VAR}` 一律替换为空串（仅 warning，实测日志可见 `The "METRICS_TOKEN" variable is not set. Defaulting to a blank string.`）。漏配 `MYSQL_PASSWORD` 时 `"" != "123456"` → **校验通过**，`MYSQL_ROOT_PASSWORD` 也变空串（`:7`）→ 生产 MySQL root 无密码上线。

对比：`SECRET_KEY` 漏配会因长度校验启动失败，`METRICS_TOKEN` 有 `settings.py:235` 的空串兜底——只有 `MYSQL_PASSWORD` 这条路径会**静默带病上线**。

### P0-3 默认配置下未认证端点可被用于 Argon2 CPU 耗尽 + 无限灌库

`app/core/settings.py:87,74`：`ENABLE_RATE_LIMIT=False`（限流默认关）、`LOGIN_IP_MAX_FAILURES=0`（IP 维度默认关）。

`app/login_guard.py:93-115` 只按 `subject`（邮箱）累加，**每次换邮箱计数恒为 1，永不触发锁定**。

`app/services/user.py:41-50` 的恒定耗时设计（用户不存在也算一次完整 Argon2）在这里反成放大器：

```python
hashed = user.password if user is not None and user.password else _DUMMY_PASSWORD_HASH
if not security.verify_password(password, hashed):   # 不存在的账号也跑满一次 KDF
```

Argon2 刻意设计为 CPU/内存昂贵，无 IP 级限流即构成低成本打满型 DoS；`POST /api/v1/member/register`（`app/api/member.py:41-49`）还无上限往 `member` 表插行。
注意 `app/login_guard.py:4` 文档写「IP 层已有全局限流兜底」——该兜底在默认配置下并不存在。

### P0-4 自举把建用户 / 播种 RBAC / 授超管拆成三个独立事务，中途失败会永久锁死系统

`app/api/auth.py:39-59`，四次 commit 分别在 `services/user.py:33`、`services/rbac.py:119`、`:162`、`services/audit.py:67`。

```python
created = await user_service.create_user(db, data)  # COMMIT #1
await rbac_service.ensure_seeded(db)  # COMMIT #2
await rbac_service.assign_role_to_user(db, created, "super_admin")  # COMMIT #3
```

第一次 commit 后用户行再也撤销不了。并发两个 bootstrap 请求（邮箱不同）会双双通过 `needs_bootstrap()`（`services/user.py:18-20`，基于 `count_users()==0`），随后在 `ensure_seeded` 撞 `permission.code` / `role.code` 唯一键，**而该函数无 try/except** → `IntegrityError`。

残局：用户已落库但无角色，且 `needs_bootstrap()` 恒为 False → `POST /api/v1/auth/bootstrap` **永久返回 409**，再也无法通过 API 创建管理员。唯一恢复手段是手工跑 `scripts/seed_rbac.py` 或改库。DB 抖动同样触发。

---

## 二、安全与正确性（P1）

### P1-1 刷新令牌轮换 GET-then-DELETE 存在 TOCTOU，且 Redis 故障时 fail-closed

`app/services/tokens.py:75-78`

```python
stored = await redis.get(f"refresh:{jti}") if redis is not None else None
if stored is None:
    raise HTTPException(status_code=401, ...)
await redis.delete(f"refresh:{jti}")
```

- **并发**：同一 refresh token 的两个并发请求（多标签页静默刷新）都读到值 → 都通过 → **都签发新令牌对**。「旧 refresh 重用即 401」的盗用检测在并发下完全失效。应改 `GETDEL` / Lua 原子化。
- **Redis 不可用**：`redis is None` 直接 401；连接异常则被 `core/exceptions.py:74-80` 兜成 500。与全项目其它旁路（黑名单 `core/deps.py:46-47`、主体撤销 `core/revocation.py:60-62`、登录锁定 `login_guard.py:54-58`）的 **fail-open** 策略相反，这是唯一 fail-closed 的旁路——Redis 一抖，全站登录态集体掉线。

### P1-2 WebSocket 不校验 `aud`，也不做主体级撤销

`app/plugins/websocket.py:122-144`：只校验签名、`type`、黑名单，**没有 `aud` 比对，没有 `is_subject_revoked`，没有 `is_active`**。对比 REST 侧 `app/core/deps.py:98-103` 三项俱全。

- 会员可用 `aud="member"` 的令牌直连后台 WS；且 `user_id` 只存裸 int（`:160`），admin id=1 与 member id=1 在 WS 侧无法区分，下游按 `meta(conn_id)["user_id"]` 判权会串号。
- 管理员删号/停用（`api/user.py:187` 写 `revoke:admin:{id}`）后，该账号的 WS 连接**存活到 access token 自然过期**（默认 30 分钟），期间持续收发业务消息。

### P1-3 审计旁路并不 fail-safe：长邮箱撞库留痕整条丢失，且回滚的是业务事务

**a) 共享会话的 commit/rollback** — `app/services/audit.py:56-74`

模块 docstring（`:3-4`）声称「审计有自己的事务边界，不跟随业务事务回滚」，但传入的 `db` 就是 `get_db` 给的业务会话（`api/auth.py:31`、`api/user.py:74` 等全部直接传）。第 67 行 `commit()` 会提前提交业务事务，第 72 行 `rollback()` 会**连带丢弃未提交的业务改动**，与承诺相反。当前调用顺序恰好都是「业务先 commit」，属潜伏雷区。

**b) 登录失败审计被列宽截断** — `target_id` 是 `String(64)`（`app/models/audit.py:26`），而 `UserLogin.email` 无 `max_length`（`app/schemas/user.py:45`，`EmailStr` 最长 254）。用 >64 字符的邮箱撞库 → MySQL 1406 → 被 `audit.py:68` 的 `except Exception` 吞掉 → **攻击者只要把邮箱填长，失败登录审计一条都不落库**，与 `api/auth.py:95` 明确写明的「登录失败要留痕」直接冲突。

### P1-4 权限缓存失效路径上 `get_redis()` 抛 RuntimeError，把旁路变成 500

`app/services/rbac.py:58-63`

```python
async def _invalidate_user_cache(user_id: int) -> None:
    redis = get_redis()  # ← 在 try 之外，RuntimeError 直接逃逸
    try:
        await redis.delete(...)
    except Exception:
        logger.warning(...)
```

`get_redis()`（`app/plugins/cache.py:21-24`）在 `_redis is None` 时抛异常。而它总是**在 `db.commit()` 之后**被调用（`services/rbac.py:175`、`:162`、`:183`）→ 数据库改动已生效、接口却返回 500。worker 进程与 `app/tasks.py` 定时任务未注入 Redis 时必然触发。缓存是旁路，却成了请求成败的决定因素。

同类：`services/rbac.py:72-73` 的 `if redis is None: return` 是**死代码**（`get_redis()` 永不返回 None）。

### P1-5 `@cached` 装饰器三处实质缺陷

`app/plugins/cache.py:27-29`

```python
raw = json.dumps([args, kwargs], default=str, sort_keys=True)
return f"cache:{prefix}:{hashlib.sha256(raw.encode()).hexdigest()}"
```

1. **命中率恒为 0**：`default=str` 会把 `AsyncSession` / `Request` / ORM 对象序列化成 `<...object at 0x7f9c...>`，**对象内存地址进了哈希输入**。任何带 `db=` / `request=` 参数的被装饰函数，每次 key 都不同 → 缓存永不命中，却照样付 `json.dumps` + sha256 开销。
2. **命中/未命中返回类型不一致**（`:66` vs `:75`）：未命中返回原始对象（`set`、`BaseModel`、`datetime`），命中返回 `json.loads` 结果（`list`、`dict`、`str`）。**只在缓存命中的那一刻行为才崩**，最难复现。
3. **键不含函数名**：两个函数共用同一 `prefix` 且入参相同会互相串数据。

### P1-6 `/metrics` 令牌使用非常量时间比较

`app/plugins/metrics.py:30`：`if auth != f"Bearer {self.token}":`。`!=` 首字节不同即返回，逐字节可测。应改 `hmac.compare_digest`。

### P1-7 注册接口 409 可枚举邮箱，且无任何节流

`app/api/member.py:41-44` 返回「邮箱已被注册」，匿名即可探测某邮箱是否已注册，与登录侧刻意「不泄露账号状态」（`services/member.py:34-37`）口径不一致。

---

## 三、健壮性与性能（P2）

| # | 位置 | 问题 |
|---|---|---|
| P2-1 | `app/main.py:24-43` | lifespan 无 `try/finally`。`task_manager.start()` / `scheduler.start()` 在 yield 前抛异常时，`redis.aclose()` 与 `async_engine.dispose()` 全部跳过 → 资源泄漏，且**整个 Web 服务因一个旁路依赖拒绝启动**。同理 `scheduler.stop()` 抛错会跳过其后的全部清理。 |
| P2-2 | `app/models/audit.py` + `alembic/versions/ef4816d0e0a4_*.py:36-38` | `created_at` 无索引（只在 actor_type/actor_id/action 建了索引）。审计表只增不删、数据量最大，`GET /api/v1/audit/logs` 带时间范围（`repositories/audit.py:54-67`）必全表扫描 + 一次 COUNT 全表扫描。 |
| P2-3 | `app/services/rbac.py:167-176`、`app/repositories/rbac.py:168-186` | 循环内 await + N+1。`set_role_permissions` 最多 **201 次串行 DB 往返**（`permission_codes` 上限 200），`set_user_roles` 最多 50+51 次。可合并为一条 `IN (...)` + 一次批量 insert。 |
| P2-4 | `docker-compose.yml:6-19` | 开发 compose 把无认证 Redis（`redis:7` 无 requirepass）与弱口令 MySQL（`root/123456`）绑到 `0.0.0.0`。同仓库 `docker-compose.prod.yml:11,33` 明确写了 `127.0.0.1:...`，说明生产侧知道区别，开发侧是疏漏。 |
| P2-5 | `frontend/src/stores/auth.ts:54-72` | `restore()` 把「权限拉取失败」等同于「未登录」：`fetchMe()` 成功但 `fetchPermissions()` 403/超时 → `user=null` + `markSession(false)`。一次权限接口抖动 = 强制登出；更糟的是清掉 `auth.has_session` 后，下次进站**连 `/user/me` 探测都不做**，用户必须手动重登，即使 Cookie 一直有效。 |
| P2-6 | `frontend/src/stores/auth.ts:31-38` | `login()` 先 `markSession(true)` 再 `fetchMe()`，任一失败 → 界面报「登录失败」但会话 Cookie 已建立。表现为「登录报错但刷新就好了」。 |
| P2-7 | `app/schemas/page.py:15-16` + `app/api/index.py:22-28` | `page` 上限 10000 × `page_size` 100 → `OFFSET 999900`；`/api/v1/info` 免鉴权且每次 `SELECT COUNT(*)`。schema 注释自己承认是 DoS 入口但未实现游标。 |
| P2-8 | `app/plugins/tasks.py:270-275`、`:653-663` | 队列满时 `asyncio.QueueFull` 冒泡成 500 而非 429，把「过载」误报成「服务端故障」，客户端无法据此退避。 |
| P2-9 | `app/services/audit.py:31-34` | IP 只取直连对端。反代部署下所有审计 `ip` 都是网关地址 → 按 IP 复盘能力归零；未来开启 `LOGIN_IP_MAX_FAILURES` 时，一个出口 IP 的失败会**锁死全部用户**。 |
| P2-10 | `app/plugins/rate_limit.py:96-142` | 限流插件的 Redis 存储后端从不关闭（lifespan 只关 `app.state.redis` 与 `async_engine`），每个进程额外持有一个未释放的连接池。 |

---

## 四、测试质量（P3）：假绿灯与盲区

当前 **264 passed / 覆盖率 90%**，但以下用例是绿的却没在验证它声称的东西：

| # | 位置 | 问题 |
|---|---|---|
| P3-1 | `tests/test_login_guard.py:64-77` | `test_login_lock_is_per_account` 断言恒真。`other@test.com` **从未被创建**，且第 74 行用的是**错误密码** `wrong1234` → 必然 401。无论锁定逻辑按账号、全局、还是整个删掉，用例都通过。 |
| P3-2 | `tests/test_middleware.py:56-64` | `test_cors_allows_patch` 既没构造 app 也没发请求，直接断言模块级常量 `_ALLOWED_METHODS`。即使 CORSMiddleware 忘了接收这些方法，用例依然通过——断言的是实现细节而非行为。 |
| P3-3 | `tests/test_i18n.py:111-114` | 插值用例的期望值 `"Permission denied"` 本身不含 `{missing}`，无论是否执行替换都成立。 |
| P3-4 | `app/core/deps.py:22` vs `tests/test_auth.py:149-159` | `_CSRF_METHODS = {"POST","PUT","PATCH","DELETE"}`，但全部 CSRF 用例**只测了 POST**。前端最常用的 `PATCH /user/{id}`、`DELETE /user/{id}` 在 Cookie 鉴权下的 CSRF 保护零覆盖。 |
| P3-5 | `tests/test_rate_limit.py:20-43` | 12 个用例全是裸 `FastAPI()` + 手动 `setup(app)`，无一处 `monkeypatch` 打开 `ENABLE_RATE_LIMIT`。**生产 `docker-compose.prod.yml:59` 显式开启限流，却没有任何真实 app 集成测试**——AGENTS.md 记载的「限流整体静默失效」历史缺陷，回归测试没覆盖让它失效的那个接线层。 |
| P3-6 | 覆盖率盲区 | `services/user.py:34-37`（IntegrityError→rollback→409）零覆盖，member 侧有（`test_member.py:226-231`）、admin 侧没有；`services/rbac.py:62-63,74,78-79`（缓存失效）零覆盖，恰是 P1-4 的缺陷路径；`tasks.py:56-59` 零覆盖。 |
| P3-7 | `tests/conftest.py:29-45` | SQLite + `StaticPool` 单连接：不强制 `String(64)` 长度（MySQL 严格模式会 1406 报错），也无法复现并发事务交错。MySQL 方言差异测不到，CI 的 MySQL job 又不设 `--cov-fail-under`。 |
| P3-8 | `tests/conftest.py:47-56` | conftest 只替换了 `get_db` 依赖，未替换 `app.core.database.AsyncSessionLocal`。`app/tasks.py:56` 的具名任务直连 `.env` 里的真实 MySQL，一旦有用例触发 `cleanup_task_history` 会**物理删除 task_run 历史数据**。 |
| P3-9 | `tests/test_tasks_redis.py:18,225,338` | 污染全局 `task_manager` 注册表且无 `clean_registry` 清理，用例间存在顺序耦合。 |

---

## 五、轻微（P4）

- `frontend/src/stores/permission.ts:29-33` — `has(code, requireAll)` 的 `requireAll` 是**死参数**，两个分支完全相同（都 `perms.includes(code)`）。配合旁边的 `hasAny`/`hasAll` 极易误导调用方。
- `Dockerfile:15` — `ENV TZ=Asia/Shanghai`，与 AGENTS.md:69「时间源统一为应用侧 UTC」的约定冲突；MySQL 容器未设 TZ（UTC），两侧差 8 小时。目前所有 `datetime.now()` 都显式带 `timezone.utc` 才没爆。
- `app/login_guard.py:101-115` — `INCR` 与 `EXPIRE` 非原子。进程在两者之间被 kill → 计数键永不带 TTL → 该账号永久累加、锁过期后立刻重新加锁，**永远恢复不了**。应改 pipeline/Lua。
- `frontend/src/api/request.ts:45-53` — `SILENT_404_PATHS` 用 `includes` 前缀匹配，`/api/v1/tasks` 会连带吞掉 `/api/v1/tasks/history` 的真实 404，界面零反馈。
- `frontend/src/views/{Member,Permission,User}ManageView.vue` — `onMounted(loadXxx)` 的 Promise 无 catch → unhandled rejection（同项目 `UserManageView.loadRoles`、`RoleManageView.loadPermissions` 都有 catch，这三处漏了）。
- `frontend/src/views/LoginView.vue:40` — 给 `validate()` 传 async 回调，Element Plus 不 await 该回调，外层 `await` 在校验结束时就 resolve。
- `app/plugins/scheduler.py:245-256` — 调度循环用循环体开始时的陈旧 `now` 计算休眠；`_wake.clear()` 在 `_fire` 之后执行，会清掉 `_fire` 期间新登记任务的唤醒事件（最多延迟 60 秒）。
- `app/api/auth.py:130-133`、`app/api/member.py:122-125` — 登出端点免鉴权（受 SameSite=Lax 保护，实际无法对他人生效）。
- `app/plugins/tasks.py:554-564` — `_recent_from_redis` 循环内 await，最多 100 次串行 Redis 往返，应改 MGET/pipeline。
- `app/plugins/rate_limit.py:42-48` — `_matches_api_route` 每个请求遍历全部路由并做正则匹配（含编译），是最外层中间件里的固定 CPU 开销。

---

## 六、建议修复顺序

1. **P0-1 + P0-2**（同一文件同一根因）：`docker-compose.prod.yml` 三处 `$$` 去掉转义改单 `$`；`settings.py:231` 改为「空串或默认值都拒绝」。改动面极小，收益是生产环境从「完全起不来」到可用。
2. **P0-4**：bootstrap 合并为单事务（或加「有用户无角色」的补偿分支），`needs_bootstrap` 改为判「是否存在 super_admin 用户」而非 `count_users()==0`。
3. **P0-3**：默认开启 IP 维度锁定（`LOGIN_IP_MAX_FAILURES` 给非零默认），或在网关侧强制限流；登录/注册接口单独收紧阈值。
4. **P1-1 / P1-2**：refresh 轮换改 `GETDEL`；WS 补齐 `aud` 校验与主体撤销。
5. **P1-3 / P1-4**：审计改独立 session（同 `services/task.py:record_run` 的 fail-safe 模式）；`get_redis()` 调用移入 try 或改返回 `None`。
6. **P3-1 ~ P3-5**：补真实断言与集成测试——这几项保护的正是 AGENTS.md 里记载的两个已修复历史缺陷（CSRF、限流静默失效），补测成本远低于再次线上排查。
