from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    data_dir: str = "./data"
    port: int = 8787
    user_id: str = "local"
    # 一道门（app/auth.py）。留空 = 没门，tailnet 内自托管默认这样；
    # 走 cloudflared / ngrok / 公网 VPS 之前**必须**填。
    access_token: str = ""
    proactive_enabled: bool = False
    # Web Push 的 VAPID sub claim。**必须是合法 mailto:**——py_vapid 只收 mailto，
    # 而 Apple 会校验域名：`mailto:…@localhost` 直接 403 BadJwtToken（实测）。
    # 自托管的人应该改成自己的邮箱，见 VAPID_SUBJECT。
    vapid_subject: str = "mailto:nostos@example.com"
    # 通知标题。伙伴该有名字，但 persona.md 现在没有名字字段（#12 引导会补）
    push_title: str = "nostos"
    # Display timezone for message stamps + per-turn time anchor (not in user_profile).
    timezone: str = "Asia/Shanghai"


settings = Settings()
