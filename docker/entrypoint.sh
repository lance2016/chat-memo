#!/bin/sh
set -e

# 数据库刚起来时可能还没就绪，compose 的 healthcheck 已经等过一轮，这里再兜个底。
echo "==> 执行数据库迁移"
alembic upgrade head

echo "==> 启动后端 (reload=${RELOAD:-0})"
if [ "${RELOAD:-0}" = "1" ]; then
    # --reload-dir 限定只监视源码目录，避免 watchfiles 去扫整个挂载卷
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        --reload --reload-dir app --reload-dir alembic
else
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
