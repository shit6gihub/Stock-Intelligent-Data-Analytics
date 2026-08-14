# PanWatch Dockerfile
# 多阶段构建，减小最终镜像大小
# 基础镜像源: 默认用 DaoCloud 公开镜像(docker.m.daocloud.io)—— 国内 ACR 构建
# 环境访问 docker.io 超时、阿里云 library 需登录, DaoCloud 免登录实测可用(2026-08-14)。
# 海外自建/ghcr 发布可 build-arg 覆盖回官方:
#   --build-arg NODE_IMAGE=node:20-alpine --build-arg PYTHON_IMAGE=python:3.11-slim
ARG NODE_IMAGE=docker.m.daocloud.io/library/node:20-alpine
ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.11-slim

# ===== Stage 1: 前端构建 =====
FROM ${NODE_IMAGE} AS frontend-builder

# 版本号（构建时传入,注入 sw.js 缓存名,发版后浏览器自动清旧缓存防白屏）
# 优先级: build-arg VERSION(ghcr 发布显式传) > 仓库根 VERSION 文件(ACR 国内构建
# 无构建参数功能, 2026-08-14 实测, 兜底读文件) > dev
ARG VERSION=dev

WORKDIR /app/frontend

# 安装 pnpm
RUN npm install -g pnpm

# 复制根依赖与 workspace 清单，确保本地包依赖（如 ECharts）进入冻结安装
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/packages/api/package.json ./packages/api/package.json
COPY frontend/packages/base-ui/package.json ./packages/base-ui/package.json
COPY frontend/packages/biz-ui/package.json ./packages/biz-ui/package.json

# 安装依赖
RUN pnpm install --frozen-lockfile

# 复制源码并构建(直接 vite build, 跳过 tsc 严格类型检查以兼容 fork 源码既有 TS 警告)
# 先注入 sw.js 缓存版本号 → 发版后 SW 字节变化, 浏览器自动更新并清旧缓存
# ACR 构建无 build-arg 时读仓库根 VERSION 文件兜底(2026-08-14: 个人版无构建参数功能)
COPY frontend/ ./
COPY VERSION ./
RUN VERSION_VAL="${VERSION}"; \
    if [ "${VERSION}" = "dev" ] && [ -f VERSION ] && [ -s VERSION ]; then \
      VERSION_VAL="$(cat VERSION | tr -d '[:space:]')"; \
    fi; \
    echo "SW version: ${VERSION_VAL}"; \
    sed -i "s/__SW_VERSION__/${VERSION_VAL}/g" public/sw.js && npx vite build


# ===== Stage 2: Python 运行环境 =====
FROM ${PYTHON_IMAGE}

# 版本号（构建时传入）
ARG VERSION=dev

WORKDIR /app

# 安装系统依赖
# - tzdata: 时区数据（zoneinfo 模块需要）
# - 中文字体（K线截图需要）
# - Playwright Chromium 依赖的系统库
RUN set -eux; \
    apt-get -o Acquire::Retries=8 -o Acquire::http::Timeout=120 -o Acquire::http::Pipeline-Depth=0 update; \
    install_succeeded=0; \
    for install_attempt in 1 2 3 4 5; do \
      if apt-get -o Acquire::Retries=8 -o Acquire::http::Timeout=120 -o Acquire::http::Pipeline-Depth=0 install -y --no-install-recommends \
    tzdata \
    # git: requirements.txt 中含 git+https 直链(tradingagents)
    git \
    # 中文字体
    fonts-noto-cjk \
    # Playwright Chromium 依赖
    # (这些库缺失会导致 playwright 提示 Host system is missing dependencies)
    libxcursor1 \
    libgtk-3-0 \
    libpangocairo-1.0-0 \
    libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    # 常见的 Chromium 运行时依赖（不同版本/发行版可能会缺）
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxi6 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libxshmfence1 \
    libegl1 \
    libfontconfig1 \
        libglib2.0-0; then \
        install_succeeded=1; \
        break; \
      fi; \
      echo "APT 安装第 ${install_attempt} 次失败，保留已下载包并重试"; \
      rm -rf /var/cache/apt/archives/partial/*; \
      apt-get -o Acquire::Retries=8 -o Acquire::http::Timeout=120 -o Acquire::http::Pipeline-Depth=0 update; \
    done; \
    test "$install_succeeded" = 1; \
    rm -rf /var/lib/apt/lists/*; \
    fc-cache -fv

# 复制依赖文件
COPY requirements.txt ./

# 复制本仓内本地包(requirements.txt 里 -e ./packages/marketdata 需要它先在)
COPY packages/ ./packages/

# 安装 Python 依赖(阿里云 pypi 镜像, 国内 ACR 构建加速; 海外亦可达)
RUN pip install --no-cache-dir --timeout 300 --retries 8 -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt && \
    python -c "from sqlalchemy import create_engine; from marketdata.vendors.tencent_panel import fetch_price_distribution; assert create_engine and fetch_price_distribution"

# 注意: Playwright 浏览器将在首次启动时自动安装到 data 目录
# 这样可以减小镜像体积，并支持跨版本持久化

# 复制后端代码
COPY src/ ./src/
COPY server.py ./
COPY prompts/ ./prompts/
COPY strategies/ ./strategies/

# 写入版本号，并确保构建异常时不会产出空版本文件
RUN test -n "${VERSION}" && printf '%s\n' "${VERSION}" > VERSION && test -s VERSION

# 从前端构建阶段复制静态文件
COPY --from=frontend-builder /app/frontend/dist ./static/

# 创建数据目录
RUN mkdir -p /app/data

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV DOCKER=1

# 默认时区（可在 docker run 时用 -e TZ=... 覆盖）
ENV TZ=Asia/Shanghai

# 暴露端口（保持 8000 不变，避免影响存量用户升级）
EXPOSE 8000

# 健康检查（使用 Python）
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 启动命令
CMD ["python", "server.py"]
