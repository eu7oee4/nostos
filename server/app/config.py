from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    data_dir: str = "./data"
    port: int = 8787
    user_id: str = "local"
    proactive_enabled: bool = False


settings = Settings()
