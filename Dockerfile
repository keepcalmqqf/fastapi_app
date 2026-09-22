# 前端构建阶段
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 使用 Python 3.12 环境进行构建
FROM python:3.12-slim
# 安装 tzdata 并固定时区（slim 镜像默认 UTC；apt 安装对已存在包为幂等操作）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai
# 安装 uv（pin 具体版本保证构建可重现）
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/
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
# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
# 执行命令
CMD ["fastapi", "run", "app/main.py"]
