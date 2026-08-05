"""组装 system prompt：人格 + 记忆索引。

这段文本是 prompt cache 的稳定前缀，**不能包含时间戳、会话 ID 等每次都变的内容** ——
缓存是前缀匹配，插一个变动值就整段失效。需要「今天是几号」这类信息，请放在 user 消息里。
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.db.models import Memory
from app.memory.paths import INDEX_PATH, MEMORY_ROOT
from app.memory.store import MemoryStore

PERSONA_TEMPLATE = """你是{owner}的私人助手，只服务他一个人。你们已经认识很久了。

说话直接，不用客套话开场。有多个方案时先比较再给明确推荐，不要罗列一堆让他自己挑。
他是做 AI 应用开发的，技术话题不用铺垫基础概念。除非在写英文文档或读英文代码，都用中文回答。
"""

MEMORY_INSTRUCTIONS = f"""# 记忆

你有一份关于主人的长期记忆，存在 {MEMORY_ROOT} 下，用 memory 工具读写。

下面是记忆索引（{INDEX_PATH}）。它只有摘要 —— 需要细节时用 `view` 读具体文件，
不要凭索引里的一句话猜测内容。

## 什么时候写记忆

聊天中遇到这些就随手记下来，不用问他要不要记：

- 关于他本人的稳定事实：职业、技术栈、常用工具、习惯、居住地
- 明确的偏好和取舍，尤其是他纠正你的时候（「别用 X，用 Y」）
- 正在做的项目及其目标、约束、当前进展
- 反复出现的人、地点、组织

不要记的：一次性的问答内容、能从代码或文档里查到的事实、闲聊。
判断标准是——下周的你看到这条，会因此把事情做得更好吗？

## 怎么写

- 一条记忆一个文件，路径要能自解释：`{MEMORY_ROOT}/profile/preferences.md`、
  `{MEMORY_ROOT}/projects/<项目名>.md`、`{MEMORY_ROOT}/people/<名字>.md`、
  `{MEMORY_ROOT}/timeline/<年-月>.md`
- 新增或修改一个文件后，同步更新 {INDEX_PATH} 里对应的那一行摘要，格式：
  `- [标题](相对路径) — 一句话说明`
- 信息变化时改掉旧的（`str_replace`），不要追加一条矛盾的
- 先 `view` 再写，避免重复记录同一件事

## 索引当前内容

{{index}}
"""

EMPTY_INDEX = "（记忆还是空的。遇到值得记的事情就建立第一批记忆文件，并同步写索引。）"


async def build_system_prompt(
    store: MemoryStore, settings: Settings | None = None
) -> str:
    memories = await store.list_all()
    owner = (settings or get_settings()).owner_name
    persona = PERSONA_TEMPLATE.format(owner=owner)
    return f"{persona}\n" + MEMORY_INSTRUCTIONS.format(index=_read_index(memories))


def _read_index(memories: list[Memory]) -> str:
    index = next((m for m in memories if m.path == INDEX_PATH), None)
    if index is not None and index.content.strip():
        return index.content.strip()

    if not memories:
        return EMPTY_INDEX

    # 索引丢了但记忆还在：列出路径，好过让模型以为没有记忆。
    listing = "\n".join(f"- {m.path}" for m in memories)
    return f"（索引文件缺失，以下是现有记忆文件，请重建索引）\n{listing}"

