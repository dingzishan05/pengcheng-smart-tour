from __future__ import annotations

import threading
from collections import deque

_LOCK = threading.Lock()
_LATENCY_WINDOW = 1000

_requests_total: int = 0
_errors_total: int = 0
_latencies_ms: deque[int] = deque(maxlen=_LATENCY_WINDOW)
_cache_hits: int = 0
_cache_misses: int = 0
_llm_calls_total: int = 0
_llm_errors_total: int = 0


def record_request(duration_ms: int, is_error: bool = False) -> None:
    global _requests_total, _errors_total
    with _LOCK:
        _requests_total += 1
        if is_error:
            _errors_total += 1
        _latencies_ms.append(int(duration_ms))


def record_cache_hit() -> None:
    global _cache_hits
    with _LOCK:
        _cache_hits += 1


def record_cache_miss() -> None:
    global _cache_misses
    with _LOCK:
        _cache_misses += 1


def record_llm_call(is_error: bool = False) -> None:
    global _llm_calls_total, _llm_errors_total
    with _LOCK:
        _llm_calls_total += 1
        if is_error:
            _llm_errors_total += 1


def _percentile(sorted_values: list[int], ratio: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, int(round(ratio * (len(sorted_values) - 1))))
    return sorted_values[index]


def snapshot() -> dict:
    with _LOCK:
        latencies = sorted(_latencies_ms)
        total_calls = _llm_calls_total
        llm_errors = _llm_errors_total
        cache_lookups = _cache_hits + _cache_misses
        payload = {
            "requests_total": _requests_total,
            "errors_total": _errors_total,
            "error_rate": round(_errors_total / _requests_total, 4) if _requests_total else 0.0,
            "p50_ms": _percentile(latencies, 0.50),
            "p90_ms": _percentile(latencies, 0.90),
            "cache_hits": _cache_hits,
            "cache_misses": _cache_misses,
            "cache_hit_rate": round(_cache_hits / cache_lookups, 4) if cache_lookups else 0.0,
            "llm_calls_total": total_calls,
            "llm_errors_total": llm_errors,
        }
    return payload


def reset_metrics() -> None:
    global _requests_total, _errors_total, _cache_hits, _cache_misses, _llm_calls_total, _llm_errors_total
    with _LOCK:
        _requests_total = 0
        _errors_total = 0
        _latencies_ms.clear()
        _cache_hits = 0
        _cache_misses = 0
        _llm_calls_total = 0
        _llm_errors_total = 0