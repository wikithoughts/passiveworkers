"""D36 rate limiter: deterministic sliding-window unit tests (time injected, no sleeps)."""
from council.net.ratelimit import RateLimiter


def test_allows_up_to_limit_then_denies():
    rl = RateLimiter()
    assert all(rl.allow("k", 3, 60, now=100) for _ in range(3))
    assert rl.allow("k", 3, 60, now=100) is False     # 4th within the window is denied


def test_window_expiry_allows_again():
    rl = RateLimiter()
    assert all(rl.allow("k", 3, 60, now=0) for _ in range(3))
    assert rl.allow("k", 3, 60, now=0) is False
    assert rl.allow("k", 3, 60, now=61) is True        # earlier hits aged out of the window


def test_denied_events_are_not_recorded():
    rl = RateLimiter()
    assert rl.allow("k", 1, 60, now=0) is True
    assert rl.allow("k", 1, 60, now=0) is False        # denied → NOT recorded (deque stays bounded)
    assert rl.allow("k", 1, 60, now=0) is False        # the single recorded hit still in window
    assert rl.allow("k", 1, 60, now=61) is True         # it ages out → allowed again


def test_limit_zero_disables_the_key():
    rl = RateLimiter()
    assert all(rl.allow("k", 0, 60, now=i) for i in range(100))


def test_keys_are_independent():
    rl = RateLimiter()
    assert rl.allow("a", 1, 60, now=0) is True
    assert rl.allow("a", 1, 60, now=0) is False
    assert rl.allow("b", 1, 60, now=0) is True          # a different key is unaffected


def test_sweep_drops_idle_keys_on_overflow():
    rl = RateLimiter(max_keys=3)
    for i in range(5):
        rl.allow(f"old{i}", 5, 60, now=0)               # 5 keys, all hit at t=0 → over capacity
    assert len(rl._hits) == 5
    rl.allow("new", 5, 60, now=10_000)                  # far future → triggers sweep of idle keys
    assert set(rl._hits) == {"new"}                     # idle 'old*' keys swept; only 'new' remains
