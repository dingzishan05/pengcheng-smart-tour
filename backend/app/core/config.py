from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "彭城智游"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://pczy:pczy@localhost:5432/pczy"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_statement_timeout_ms: int = 3000

    redis_url: str = "redis://localhost:6379/0"
    redis_timeout_ms: int = 500

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 20
    llm_max_concurrency: int = 20
    llm_max_queue: int = 50

    embedding_model: str = ""

    amap_web_key: str = ""
    amap_js_key: str = ""
    # 高德 Web 服务 Key 的实测并发上限约为 4 QPS，超过会返回 CUQPS_HAS_EXCEEDED_THE_LIMIT。
    amap_qps: int = 3
    amap_call_budget: int = 600

    weather_key: str = ""

    admin_initial_password: str = ""
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 2
    admin_login_fail_limit: int = 5
    admin_lock_minutes: int = 10

    optimizer_time_limit: int = 8
    optimizer_max_candidates: int = 12
    optimizer_candidate_pool_limit: int = 200
    route_cache_ttl: int = 86400
    route_db_max_age_days: int = 7
    weather_cache_ttl: int = 3600
    poi_cache_ttl: int = 21600
    data_validity_days_official: int = 90
    data_validity_days_field: int = 30
    data_validity_days_report: int = 14
    clarify_max_rounds: int = 2
    rag_similarity_threshold: float = 0.75
    rag_weak_threshold: float = 0.60
    tour_total_budget_seconds: int = 30
    explain_fallback_reserve_seconds: int = 6
    user_input_max_length: int = 2000

    cors_origins: str = ""

    def check_required_secrets(self) -> list[str]:
        required = ["llm_api_key", "llm_base_url", "llm_model", "amap_web_key", "jwt_secret"]
        return [name for name in required if not getattr(self, name)]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
