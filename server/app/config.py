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

    # --- 会话段与重铸（docs/SEGMENTS.md）------------------------------------
    # 软线 / 硬线按**字符**算（段内 user + assistant 正文长度之和，加 episode 块），
    # 不是 token：没有分词器，字符是确定的、可测的代理。中文在 DeepSeek 上大约
    # 0.6 token / 字，20000 字 ≈ 12k token。日志里每次提炼都带真实 prompt_tokens，
    # 拿它校准这两个数。
    segment_soft_chars: int = 6000
    segment_hard_chars: int = 20000
    # 重铸时保留的最近轮数 = episode 算「新鲜」的最大轮数。**同一个数**：尾巴必须
    # 盖住 episode 之后的全部对话，改要一起改。
    segment_tail_turns: int = 10
    # 闲置多久算「闲置」（分钟）。必须小于缓存 TTL，闲置提炼才是缓存读。
    segment_idle_minutes: int = 15
    # 距上一次任何 LLM 调用多久算缓存已死（分钟）。DeepSeek 拿不到官方值，按比闲置
    # 阈值略大估；Claude 侧是 60。用提炼那次的 cache_hit_tokens 校准。
    segment_cache_ttl_minutes: int = 20
    # 滚动块（episode）长度上限（字符）。提示词让模型自己压，这是兜底截断。
    episode_max_chars: int = 800

    # --- 记忆检索的向量服务（docs/MIN_MEMORY.md「检索」）-----------------------
    # 留空 = 不做向量，召回退回「关键词 + 新的在前」。DeepSeek 没有 embeddings 接口，
    # 09-14 拍板先接本机 ombre-ollama 容器里的 bge-m3（1024 维，纯 CPU，不出网不要 key）：
    #   宿主机跑：EMBED_BASE_URL=http://127.0.0.1:11434（要有 nostos-ollama-proxy 转发）
    #   compose 跑：EMBED_BASE_URL=http://ombre-ollama:11434（docker-compose.ombre.yml 挂进那个网络）
    # api_format：ollama（POST /api/embed）或 openai（POST /v1/embeddings，如硅基流动，
    # base_url 要带 /v1、模型名写全 BAAI/bge-m3，Ombre README 记的两个坑）。
    embed_base_url: str = ""
    embed_model: str = "bge-m3"
    embed_api_format: str = "ollama"
    embed_api_key: str = ""
    embed_timeout_seconds: float = 20.0
    # ollama 专用：模型在内存里留多久。默认 5m，空闲后第一次嵌入要重新加载（实测 11 秒）。
    # -1 = 常驻。bge-m3 1.2 GB，和 cassette 共用那个容器，常驻对它也只有好处。
    embed_keep_alive: str = "-1"
    # 召回：query 拼最近几轮 + 当前句，上限字符；两路各取前 N；融合后取前 K；RRF 常数
    recall_query_turns: int = 3
    recall_query_max_chars: int = 1800
    recall_per_path: int = 32
    recall_top_k: int = 16
    recall_rrf_k: int = 60
    # 关键词一路：共有二元组少于这个数不算命中（单个二元组撞上是噪声，会被 RRF 顶到榜首）
    recall_keyword_min_common: int = 2


settings = Settings()
