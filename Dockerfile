# 前端构建阶段
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 使用 Python 3.12 环境进行构建
FROM python:3.15-slim
# 时区显式固定为 UTC（slim 镜像本就是 UTC，写出来是为了防止被基础镜像或编排层改掉）。
# 不能设成本地时区：AGENTS.md 约定「时间源统一为应用侧 UTC」，所有入库与比较都用
# 带 tz 的 utc_now()；容器 TZ 一旦改成本地时区，日志时间戳、以及任何一处漏写
# tzinfo 的 datetime.now() 都会与 MySQL 侧（容器未设 TZ，同样 UTC）差 8 小时，
# 且只在生产环境出现，本地与测试都复现不了。
# 保留 tzdata 是因为 SCHEDULER_TIMEZONE 等配置经 zoneinfo 解析，缺库会直接抛
# ZoneInfoNotFoundError（Windows 上踩过同一个坑，见 AGENTS.md）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=UTC
# 安装 uv（pin 具体版本保证构建可重现）
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/
# 工作目录
WORKDIR /app
# 复制依赖文件
COPY pyproject.toml uv.lock ./
# 安装依赖（不含 dev 依赖）
RUN uv sync --frozen --no-dev --no-install-project
# 复制所有文件
COPY . .
# 复制前端构建产物（由后端托管）
COPY --from=frontend /build/dist ./frontend/dist
# 使用 uv 创建的虚拟环境
ENV PATH="/app/.venv/bin:$PATH"
# 非 root 运行
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
# 暴露端口
EXPOSE 8000
# 健康检查：探 /health/live（只看进程存活，不探 MySQL/Redis），避免依赖抖动被
# 放大成容器重启；依赖就绪判断交给 /health/ready 由编排层按需使用。
# start-period 覆盖启动期跑 alembic upgrade head 的时间，避免迁移期间被误判。
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=40s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"]
# 启动前先执行数据库迁移：漏跑迁移会让所有依赖表结构的接口直接 500，
# 这里让它成为启动的必经步骤（迁移失败即容器退出，不会带病对外提供服务）。
# exec 保证 uvicorn 接管 PID 1，信号能正常传递；worker 数按容器 CPU 用 WEB_CONCURRENCY 调整。
CMD ["sh", "-c", "alembic upgrade head && exec fastapi run app/main.py --workers ${WEB_CONCURRENCY:-2}"]
