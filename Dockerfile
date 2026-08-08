FROM python:3.12-slim

# 从官方镜像拷 uv 二进制，比 pip install uv 快得多
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 关键：虚拟环境放在 /opt/venv，而不是项目里的 .venv。
# 开发时项目目录是从 macOS 挂进来的，里面的 .venv 装的是 darwin/arm64 的包，
# 容器里用不了；放到挂载点之外就彻底避开这个冲突。
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# pg_dump / pg_restore 用于备份与恢复，是标准工具，比自己写导出可靠
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只拷依赖清单，让依赖层能被缓存 —— 改业务代码时不会重装依赖。
# 含 dev 组：这是开发用镜像，要能在容器里直接跑 pytest。
COPY pyproject.toml uv.lock ./
# uv.lock 记录了生成锁文件时的制品域名；只给 frozen sync 换 index 仍可能继续
# 请求 files.pythonhosted.org。先按构建参数刷新下载来源（保留锁定版本），再安装。
# 默认仍是官方 PyPI；Compose 针对国内网络提供 TUNA 默认值，也可在 .env 覆盖。
ARG UV_DEFAULT_INDEX=https://pypi.org/simple
ARG INSTALL_OBS=0
RUN uv lock --refresh --default-index "$UV_DEFAULT_INDEX" \
    && if [ "$INSTALL_OBS" = "1" ]; then \
         uv sync --frozen --extra obs --default-index "$UV_DEFAULT_INDEX"; \
       else \
         uv sync --frozen --default-index "$UV_DEFAULT_INDEX"; \
       fi

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 开发时代码由 volume 挂载覆盖；这份 COPY 是为了镜像能独立运行（不挂载也能起）
COPY . .

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
