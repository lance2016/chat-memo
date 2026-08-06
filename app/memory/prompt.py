"""组装 system prompt：人格 + 记忆索引 + 知识库说明 + 用户自定义指令。

这段文本是 prompt cache 的稳定前缀，**不能包含时间戳、会话 ID 等每次都变的内容** ——
缓存是前缀匹配，插一个变动值就整段失效。需要「今天是几号」这类信息，请放在 user 消息里。

**自定义指令和记忆是两回事**，虽然都进 system prompt：

| | 谁写 | 谁能改 | 会不会被整理 |
|---|---|---|---|
| 记忆（`/memories`） | 模型自己 | 模型 + 用户 | 会，每日整理去重修正 |
| 自定义指令 | 用户 | **只有用户** | 不会 |

分开的理由是**权威性**：自定义指令是「我说了算」的那部分，模型不能覆盖、
每日整理不能改写。混进记忆层的话，整理任务会把它当成一条普通记忆去重掉。
主流方案都是这么切的 —— ChatGPT 的 custom instructions vs memory、
Claude Projects 的 instructions vs knowledge、Claude Code 的 CLAUDE.md。
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

# 稳定文本，不含任何变动内容 —— 和其余段落一样是 prompt cache 前缀的一部分。
# 只在 vault 挂载了（settings.vault_path 非空）才出现，和 kb 工具的注册同一开关。
KB_INSTRUCTIONS = """
# 知识库

主人的 Obsidian 笔记库对你开放了只读访问：

- 找东西先用 `kb_search`（子串搜文件名和内容，`tag:#标签` 只搜标签），浏览目录才用 `kb_list`
- `kb_read` 读具体笔记，`kb_backlinks` 沿双链找相关笔记
- 笔记是主人亲手写的，不是你的记忆：引用时注明文件路径，不要改述成你「记得」的事
- 知识库不可写。值得长期记住的结论，写进你自己的记忆（memory 工具）
"""

# 放在最末尾：system prompt 的结尾是指令遵循最强的位置，而这段的权威性最高。
# 对 prompt cache 没有影响 —— 整个 system 是一个缓存块，块内顺序不影响命中。
CUSTOM_INSTRUCTIONS_TEMPLATE = """
# {owner}的额外指令

以下是他自己写的指令，**优先级高于上面的所有默认设定**，冲突时以这里为准。
这段由他手动维护，不属于你的记忆，你既不能也不需要用 memory 工具修改它。

{instructions}
"""


async def build_system_prompt(
    store: MemoryStore,
    settings: Settings | None = None,
    *,
    include_kb: bool = True,
) -> str:
    settings = settings or get_settings()
    memories = await store.list_all()
    owner = settings.owner_name
    persona = PERSONA_TEMPLATE.format(owner=owner)
    prompt = f"{persona}\n" + MEMORY_INSTRUCTIONS.format(index=_read_index(memories))

    # include_kb=False 给没挂 kb 工具的场景用（每日整理）—— 提示词里提了工具却不注册，
    # 模型会困惑甚至试图调用。
    if include_kb and settings.vault_path:
        prompt += KB_INSTRUCTIONS

    instructions = settings.custom_instructions.strip()
    if instructions:
        prompt += CUSTOM_INSTRUCTIONS_TEMPLATE.format(
            owner=owner, instructions=instructions
        )
    return prompt


def _read_index(memories: list[Memory]) -> str:
    index = next((m for m in memories if m.path == INDEX_PATH), None)
    if index is not None and index.content.strip():
        return index.content.strip()

    if not memories:
        return EMPTY_INDEX

    # 索引丢了但记忆还在：列出路径，好过让模型以为没有记忆。
    listing = "\n".join(f"- {m.path}" for m in memories)
    return f"（索引文件缺失，以下是现有记忆文件，请重建索引）\n{listing}"

