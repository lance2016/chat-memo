from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5433/chat"

    # 助手怎么称呼主人。写进 system prompt，不要硬编码真名在代码里。
    owner_name: str = "用户"

    # anthropic | deepseek
    provider: str = "anthropic"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
