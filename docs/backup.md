# 备份与恢复

**没恢复过的备份不是备份。** 这份文档的重点是最后那节演练 —— 前面两节只是让备份
存在，而备份存在和备份能用之间隔着一次真正的 `pg_restore`。

下面每条命令都在 2026-08-08 真跑过一遍，输出附在演练那节里。

## 备份什么，为什么是两份

| 产物 | 用途 | 位置 |
|---|---|---|
| `chat-<时间戳>.dump` | **完整恢复**：对话、记忆、版本历史、时间线、埋点 | `backups/` |
| `memories/` 文件树 | **给人看**：能 grep、能用编辑器打开、能 git 管理 | `backups/memories/` |

两份都要。dump 是二进制的，出事时能一键还原但看不了；`.md` 树反过来 ——
记忆平时只以数据库行的形式存在，磁盘上没有任何文件，这是唯一把它们落成真实文件的地方。

## 自动备份

默认开，每天一份，留最近 14 份。开关和份数在**设置页**改（`backup_auto` / `backup_keep`）。

**补跑式，不是定时触发式。** 判据是「今天有没有备份过」而不是「到点了没有」——
和通知扫描同一条教训（`app/notify/sweep.py`）：进程一重启计时器就从头开始，
笔记本凌晨多半在睡眠，定时触发在这台机器上必然漏，查询式则是睡醒就补。

**判据不需要建表**：dump 的文件名里带日期，备份目录本身就是那份记录。
少一张表、少一次迁移，也不会出现「表说备份过了但文件被删了」的假象。

手动触发：设置页 → 数据与备份 → 创建备份，或 `POST /api/jobs/backup`。

⚠️ **dump 失败时不轮换**。否则会在「今天没备份成功」的情况下顺手删掉旧的好备份 ——
那是备份系统最不该犯的错。

## ⚠️ 备份和数据在同一块盘

`backups/` 挂在同一台机器上（compose 的 `./backups:/backups`）。**磁盘坏了，
数据和备份一起走。** 这不是代码能解决的问题，是个挂载决策，三选一：

```yaml
# docker-compose.yml，api 服务的 volumes
- ~/Library/Mobile Documents/com~apple~CloudDocs/chat-backups:/backups   # iCloud
- /Volumes/外置盘/chat-backups:/backups                                   # 外置盘
```

或者留在本地，另配一条 `rsync` 到别处的定时任务。**只要不是同一块盘，哪种都行。**

## 恢复演练

这一节是重点。命令按顺序抄就行。

### 1. 起一个干净的 Postgres

不要直接往生产库恢复 —— 演练的目的是验证 dump 可用，不是覆盖现有数据。

```bash
docker run -d --rm --name chat-restore-drill \
  -e POSTGRES_USER=chat -e POSTGRES_PASSWORD=chat -e POSTGRES_DB=chat \
  -p 5434:5432 pgvector/pgvector:pg17

until docker exec chat-restore-drill pg_isready -U chat -d chat; do sleep 2; done
```

⚠️ **镜像必须是 `pgvector/pgvector`**，不能用官方 `postgres` —— dump 里有 vector
扩展的痕迹，官方镜像装不了这个扩展，恢复会报错。

### 2. 还原

```bash
docker cp backups/chat-<时间戳>.dump chat-restore-drill:/tmp/restore.dump

docker exec chat-restore-drill sh -lc '
  psql -U chat -d chat -c "CREATE EXTENSION IF NOT EXISTS vector;"
  pg_restore -U chat -d chat --no-owner --no-privileges /tmp/restore.dump
'
```

两个参数的理由：`--no-owner` 和 `--no-privileges` 让 dump 里的属主/权限设定不生效 ——
恢复目标的角色名很可能和原库不同，带着它们恢复会报一堆 role does not exist。

### 3. 核对（**这一步不能省**）

行数对上只能说明表在，还要抽查内容：

```bash
docker exec chat-restore-drill psql -U chat -d chat -tAc "
  select 'conversations='||count(*) from conversations
  union all select 'messages='||count(*) from messages
  union all select 'memories='||count(*) from memories
  union all select 'timeline='||count(*) from timeline_items
  union all select 'memory_versions='||count(*) from memory_versions;"

# 内容抽查：记忆正文真的在，而不是一堆空行
docker exec chat-restore-drill psql -U chat -d chat -tAc \
  "select left(content, 60) from memories where path='/memories/MEMORY.md';"
```

### 4. 让真正的后端连上去跑一次

**这一步才是真正的验收。** 前面证明的是数据在，这一步证明应用能用它。

```bash
docker run --rm -d --network host --name chat-restore-check \
  -e DATABASE_URL="postgresql+asyncpg://chat:chat@127.0.0.1:5434/chat" \
  -e RELOAD=0 -e TZ=Asia/Shanghai \
  -v "$PWD/app:/app/app" -v "$PWD/alembic:/app/alembic" \
  -v "$PWD/alembic.ini:/app/alembic.ini:ro" \
  chat-api:latest

curl -s localhost:8000/health
curl -s "localhost:8000/api/conversations?limit=3"
curl -s localhost:8000/api/memories/audit
```

entrypoint 会先跑 `alembic upgrade head`，所以顺带验证了「旧 dump + 新代码」
这条升级路径 —— 那正是真出事时最可能的组合。

### 5. 清理

```bash
docker stop chat-restore-check chat-restore-drill
```

### 演练记录

**2026-08-08**，dump `chat-20260808-194705.dump`（108 KB）：

| 表 | 备份前 | 恢复后 |
|---|---|---|
| conversations | 42 | 42 ✓ |
| messages | 233 | 233 ✓ |
| memories | 19 | 19 ✓ |
| timeline_items | 4 | 4 ✓ |
| memory_versions | 48 | 48 ✓ |

后端连上恢复出的库正常启动，`/health` 返回 ok，API 读得到会话，
记忆索引校验也能跑（报「12 条描述超长」——和生产库一致，说明连索引内容都对上了）。

**下次演练**：换机器、升级 Postgres 大版本、或者改了任何 migration 之后。
平时半年一次足够 —— 但真的要做，不是记在文档里。
