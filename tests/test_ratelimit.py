"""D36 rate limiter: deterministic sliding-window unit tests (time injected, no sleeps)."""
from passiveworkers.net.ratelimit import RateLimiter


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
    # the first overflow (old3) swept and evicted the LRU key back to the cap; old4 arrived inside
    # the same throttle interval, so no second sweep yet (F16)
    assert len(rl._hits) == 4 and "old0" not in rl._hits
    rl.allow("new", 5, 60, now=10_000)                  # far future → triggers sweep of idle keys
    assert set(rl._hits) == {"new"}                     # idle 'old*' keys swept; only 'new' remains


def test_sweep_is_throttled_while_over_capacity():
    """F16: once over max_keys, the O(n) sweep used to run on EVERY allow(); now at most once per
    _SWEEP_EVERY_S, however many calls arrive in between."""
    from passiveworkers.net import ratelimit as RL
    rl = RateLimiter(max_keys=10)
    sweeps = []
    real = rl._sweep
    rl._sweep = lambda now: (sweeps.append(now), real(now))
    for i in range(1000):                               # key rotation: every call is a fresh key
        rl.allow(f"k{i}", 5, 60, now=100 + i * 0.001)   # 1000 calls inside one second
    assert len(sweeps) == 1
    rl.allow("later", 5, 60, now=100 + RL._SWEEP_EVERY_S + 1)
    assert len(sweeps) == 2                             # the throttle re-opens after the interval


def test_sweep_evicts_oldest_keys_when_none_are_idle():
    """F16: rotating keys keep every entry fresh, so the idle-horizon sweep alone never shrank the
    table. A sweep now evicts the least-recently-hit keys down to max_keys."""
    rl = RateLimiter(max_keys=3)
    for i in range(3):
        rl.allow(f"k{i}", 5, 60, now=i)
    rl.allow("k0", 5, 60, now=3)                        # k0 is now the MOST recently hit
    rl.allow("k3", 5, 60, now=4)                        # over the cap → sweep, nothing idle
    assert set(rl._hits) == {"k0", "k2", "k3"}          # k1 (least recently hit) evicted
    assert rl.allow("k0", 2, 60, now=5) is False        # survivors keep their windows (k0 has 2 hits)
