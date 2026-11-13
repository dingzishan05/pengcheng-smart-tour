import time

from app.clients.circuit_breaker import CircuitBreaker


def test_breaker_stays_closed_below_threshold():
    breaker = CircuitBreaker("t1", failure_threshold=3, open_seconds=0.1)
    assert breaker.allow_request()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.allow_request()


def test_breaker_opens_at_threshold():
    breaker = CircuitBreaker("t2", failure_threshold=2, open_seconds=60)
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.allow_request()


def test_breaker_half_open_then_recover():
    breaker = CircuitBreaker("t3", failure_threshold=1, open_seconds=0.05)
    breaker.record_failure()
    assert not breaker.allow_request()
    time.sleep(0.06)
    assert breaker.allow_request()
    breaker.record_success()
    assert breaker.allow_request()


def test_breaker_half_open_failure_reopens():
    breaker = CircuitBreaker("t4", failure_threshold=1, open_seconds=0.05)
    breaker.record_failure()
    time.sleep(0.06)
    assert breaker.allow_request()
    breaker.record_failure()
    assert not breaker.allow_request()


def test_breaker_open_blocks_external_calls():
    breaker = CircuitBreaker("t5", failure_threshold=1, open_seconds=60)
    breaker.record_failure()
    calls = 0

    def fake_call():
        nonlocal calls
        calls += 1

    if breaker.allow_request():
        fake_call()
    assert calls == 0