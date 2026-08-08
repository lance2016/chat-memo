from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 环境变量只承载启动所需的密钥和基础设施配置。
    # 可由用户调整的运行时配置仍保留代码默认值，并由 settings_store 的数据库覆盖层管理。
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5433/chat"

    # 助手怎么称呼主人。写进 system prompt，不要硬编码真名在代码里。
    owner_name: str = "用户"

    # 用户手写的指令，原样追加到 system prompt 末尾。和记忆的区别见 memory/prompt.py：
    # 这段只有用户能改，每日整理不会碰它。留空则整段不出现。
    custom_instructions: str = ""

    # anthropic | deepseek
    provider: str = "deepseek"

    # 新模型目录的全局默认。为空时兼容旧的 provider/model 配置。
    chat_model_profile_id: int | None = None
    consolidate_model_profile_id: int | None = None

    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    max_tokens: int = 64000
    effort: str = "high"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    # 保留现有本地部署的默认行为；需要调整时请在设置页修改，而不是继续扩充 .env。
    deepseek_max_tokens: int = 12_800
    # 全局默认是否思考。单次请求可以覆盖，会话级覆盖见 conversations.thinking。
    # 实测 DeepSeek 关掉思考后工具调用仍正常，不像 Claude 有故障模式。
    deepseek_thinking: bool = False

    # 单人使用，仅防止端口意外暴露；空值表示不校验。
    api_key: str = ""

    log_level: str = "INFO"
    log_color: bool = True
    # HTTP 访问日志。聊天已有独立叙事日志，嫌吵可以关掉
    log_access: bool = True
    # pretty = 本地人读；json = 容器日志采集器读
    log_format: str = "pretty"

    # ---- Phoenix / OpenTelemetry ----
    # 默认开。依赖已进主依赖、Phoenix 容器随 compose 常起，没有理由默认关着 ——
    # 「可选」的实际结果是容器在跑、依赖没装、一条 trace 都没有。
    # 这一项和下面的 obs_capture_content 都在设置页可改，改完立刻生效
    # （见 app/obs/tracing.py 的 apply_tracing）。
    obs_tracing: bool = True
    # Phoenix 里存不存完整 prompt / 回复正文。关掉只留 token 数、延迟和工具链路。
    # 这是隐私开关：记忆正文会跟着对话一起进 Phoenix。
    obs_capture_content: bool = True
    phoenix_collector_endpoint: str = "http://phoenix:6006"
    obs_project_name: str = "chat-memo"
    # 聊天 POST 和后台任务是主链路；GET 多数只是前端轮询或健康检查，默认不建 span。
    obs_trace_reads: bool = False
    # 只有这些 HTTP 入口会创建请求级 span；模型子 span 和后台任务不受影响。
    obs_trace_http_paths: str = "/api/chat,/api/jobs/consolidate"

    cors_origins: list[str] = ["http://localhost:3000"]

    # 整理任务专用模型。整理对质量最敏感、频率最低，值得用更好的模型；
    # 日常聊天照旧走 deepseek_model。留空表示和聊天用同一个。
    consolidate_model: str = ""

    # ---- 标题生成（可选，优先走硅基流动）----
    # 标题就是「一句话概括用户想干什么」，要的是快，不是推理。聊天模型在这件事上
    # 太贵也太慢（实测为一个 16 字标题烧掉 127~542 个思考 token、2.4~21.5 秒，
    # 而标题质量并没有更好），所以单独走一条免费的 OpenAI 兼容模型链路。
    # 配了硅基流动 key 就使用免费的 Qwen3-8B；未配置时兼容旧的智谱配置，
    # 两者都没有才退回聊天 provider，同样关掉思考。
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_title_model: str = "Qwen/Qwen3-8B"
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    title_model: str = "glm-4.7-flash"

    # 自动每日整理默认关闭：进程一重启计时器就从头开始，笔记本凌晨多半是睡眠状态，
    # 这个定时器很容易整天不触发。手动 POST /api/jobs/consolidate 更可靠。
    # 自动备份。补跑式（查「今天备份过没有」），不做精确定时 —— 笔记本凌晨在睡眠，
    # 定时器必然漏，查询式则是睡醒就补。见 app/backup.py 的 is_due。
    backup_auto: bool = True
    # 留最近几份 dump。不轮换的话磁盘会被慢慢吃满，而磁盘满的第一个症状
    # 通常是**别的东西先坏**。
    backup_keep: int = 14

    consolidate_auto: bool = False
    consolidate_hour: int = 4

    # agent loop 单次请求内允许的最大工具轮次，防止失控循环。
    max_tool_iterations: int = 12

    # 每轮发给模型的历史字符预算，超出就从最老的一端丢整轮（见 chat/service.trim_history）。
    # 没有这个上限的话，长会话会一直线性膨胀，最终撞上下文窗口 —— 那之后该会话
    # **每一条消息都 400**，等于永久损坏，和 sanitize_history 防的是同一类事故。
    # 12 万字符对中文约合 8～10 万 token，给 128k 窗口留足了 system prompt 和输出的余量。
    history_max_chars: int = 120_000

    # 记录每次发给模型的完整请求体，供 /api/debug/requests 查、日志里打轮廓。
    # 默认关：开着会把完整对话历史留在进程内存里。
    debug_prompts: bool = False

    # Obsidian vault 的挂载点（compose 把宿主机 VAULT_PATH 只读挂到 /vault 并注入本值）。
    # 留空 = 不启用知识库工具。基础设施配置，只能改 .env —— 挂载点本来就要改 compose 才能变。
    vault_path: str = ""

    # ---- 主动通知 ----
    # 默认关。开之前得先配好通道，否则 ticker 每分钟空转还占一次查询。
    notify_enabled: bool = False
    # 启用的通道，逗号分隔。目前只有 bark；加了 Web Push 之后写 "bark,webpush"。
    notify_channels: str = "bark"
    # 每天早上一条：今天有什么、什么逾期了、什么待确认挂太久了。
    notify_briefing: bool = True
    notify_briefing_hour: int = 8
    # 全天事项按当天几点提醒。全天事项的 starts_at 是当地 00:00，
    # 直接减提前量会在半夜响 —— 见 app/notify/schedule.py。
    notify_all_day_hour: int = 9
    # kind 没有默认提前量时用这个。
    notify_default_lead_minutes: int = 15
    # 补跑窗口：开始时间已经过去这么久的事项不再单独推送，交给每日简报兜底。
    # 没有这道闸，离线一周后一开机会被一周的提醒糊满屏幕。
    notify_catchup_hours: int = 6
    # 用便宜模型把提醒写成人话（走标题那条链路）。关掉则用模板。
    notify_smart_copy: bool = True
    # 通知点开后跳转的地址前缀，例如 http://192.168.1.10:13000。
    # 留空则通知不带链接 —— 手机点开一个 localhost 只会失败。
    notify_public_base_url: str = ""
    # 通知链路上每一次外部调用的上限：推送通道的 POST，以及生成文案的模型调用。
    # 文案那次尤其需要 —— ticker 是单条循环，一次卡住的调用会把之后所有提醒拖死。
    notify_timeout: int = 10

    # Bark（iOS 自建推送）。服务端 POST 一个 URL 就完事，手机不需要开着浏览器。
    bark_server: str = "https://api.day.app"
    # 放在可写设置里而不是 ENV_ONLY：它不是能造成损失的密钥（泄露的后果是别人能给你
    # 发通知），而配置它的场景恰恰是「在手机上装好 App，回到设置页粘贴一次」。
    bark_key: str = ""
    bark_sound: str = ""
    # 通知左侧的图标 URL，必须是手机能访问到的地址。
    bark_icon: str = ""

    # ---- 文字转语音（本地 mlx-audio，OpenAI 兼容接口）----
    # 地址算基础设施，只能改 .env。容器里要用 host.docker.internal 才能回到宿主机。
    tts_base_url: str = "http://127.0.0.1:8001"
    # Hugging Face hub 缓存目录。Compose 会把宿主机缓存只读挂载到这里。
    # 留空时直跑后端会自动使用 ~/.cache/huggingface/hub。
    tts_model_cache: str = ""
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

    # ---- 语音转文字（复用同一个 mlx-audio 服务）----
    asr_model: str = "mlx-community/Qwen3-ASR-1.7B-8bit"
    # 明确语言可跳过自动语言判断；需要混合语言时可在设置页切回 Auto。
    asr_language: str = "Chinese"
    # 浏览器语音输入通常很短。限制生成长度可避免静音/噪声导致的异常长推理。
    asr_max_tokens: int = 512
    # 浏览器录音先完整上传再转写。限制请求体，避免异常客户端撑爆 API 内存。
    asr_max_bytes: int = 25 * 1024 * 1024
    asr_timeout: int = 180


@lru_cache
def get_settings() -> Settings:
    return Settings()
