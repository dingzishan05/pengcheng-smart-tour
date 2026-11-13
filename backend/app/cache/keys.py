from app.core.config import settings

ROUTE_TTL = settings.route_cache_ttl
WEATHER_TTL = settings.weather_cache_ttl
POI_TTL = settings.poi_cache_ttl
EMBEDDING_TTL = 86400
SOURCES_TTL = 300


def route_key(origin_geo: str, dest_geo: str, mode: str) -> str:
    # v2：旧缓存把高德 v5 的 cost.duration 解析成了空值，升级前缀强制失效重建。
    return f"route:v2:{origin_geo}:{dest_geo}:{mode}"


def weather_key(city: str, date_str: str) -> str:
    return f"weather:{city}:{date_str}"


def poi_active_key() -> str:
    return "poi:active"


def price_key(poi_id: int) -> str:
    return f"price:{poi_id}"


def embedding_key(query_hash: str) -> str:
    return f"emb:{query_hash}"


def sources_status_key() -> str:
    return "sources:status"