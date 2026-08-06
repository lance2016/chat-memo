from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5433/chat"

    # 助手怎么称呼主人。写进 system prompt，不要硬编码真名在代码里。
    owner_name: str = "用户"

    # 用户手写的指令，原样追加到 system prompt 末尾。和记忆的区别见 memory/prompt.py：
    # 这段只有用户能改，每日整理不会碰它。留空则整段不出现。
    custom_instructions: str = ""

    # anthropic | deepseek
    provider: str = "deepseek"

    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    max_tokens: int = 64000
    effort: str = "high"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_max_tokens: int = 8000
    # 全局默认是否思考。单次请求可以覆盖，会话级覆盖见 conversations.thinking。
    # 实测 DeepSeek 关掉思考后工具调用仍正常，不像 Claude 有故障模式。
    deepseek_thinking: bool = True

    # 单人使用，仅防止端口意外暴露；空值表示不校验。
    api_key: str = ""

    log_level: str = "INFO"
    log_color: bool = True
    # HTTP 访问日志。聊天已有独立叙事日志，嫌吵可以关掉
    log_access: bool = True

    cors_origins: list[str] = ["http://localhost:3000"]

    # 整理任务专用模型。整理对质量最敏感、频率最低，值得用更好的模型；
    # 日常聊天照旧走 deepseek_model。留空表示和聊天用同一个。
    consolidate_model: str = ""

    # 自动每日整理默认关闭：进程一重启计时器就从头开始，笔记本凌晨多半是睡眠状态，
    # 这个定时器很容易整天不触发。手动 POST /api/jobs/consolidate 更可靠。
    consolidate_auto: bool = False
    consolidate_hour: int = 4

    # agent loop 单次请求内允许的最大工具轮次，防止失控循环。
    max_tool_iterations: int = 12

    # 记录每次发给模型的完整请求体，供 /api/debug/requests 查、日志里打轮廓。
    # 默认关：开着会把完整对话历史留在进程内存里。
    debug_prompts: bool = False

    # Obsidian vault 的挂载点（compose 把宿主机 VAULT_PATH 只读挂到 /vault 并注入本值）。
    # 留空 = 不启用知识库工具。基础设施配置，只能改 .env —— 挂载点本来就要改 compose 才能变。
    vault_path: str = ""

    # ---- 文字转语音（本地 mlx-audio，OpenAI 兼容接口）----
    # 地址算基础设施，只能改 .env。容器里要用 host.docker.internal 才能回到宿主机。
    tts_base_url: str = "http://127.0.0.1:8001"
    # off = 只出文字 | manual = 消息旁给播放按钮 | auto = 回答完自动朗读
    tts_mode: str = "off"
    tts_model: str = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-6bit"
    tts_voice: str = "Vivian"
    tts_lang_code: str = "Chinese"
    tts_instruct: str = "用温柔、自然、亲切的语气说话，像朋友聊天一样，语速稍慢。"
    tts_format: str = "mp3"
    # 边合成边发。实测同一段话首字节 6.97s → 1.12s，没有理由关掉，
    # 留个开关是为了排查「是不是流式导致音频损坏」这类问题。
    tts_stream: bool = True
    # 服务端要的是倍率（1.0），这里存百分比 —— 整数才好在设置页里渲染和校验。
    tts_speed_percent: int = 100
    # 朗读长度上限。模型回复动辄上千字，全念完既慢又会撞服务端的 max_tokens。
    tts_max_chars: int = 800
    tts_timeout: int = 180
    # 启动后台合成一个字，把权重加载进 MLX。不预热的话这十几秒会算在
    # 用户第一次点播放的头上。服务没起来时静默跳过，不影响启动。
    tts_warmup: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
