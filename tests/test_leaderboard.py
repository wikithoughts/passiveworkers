"""D44 — operator leaderboard. Pseudonymous (owner only); reputation board needs >=1 rating; the
credits board ranks by lifetime EARNED (not an untouched starter grant); escrow is hidden; HTTP
inputs are clamped."""
import importlib

import pytest


def _reload():
    for m in ("passiveworkers.ledger", "passiveworkers.net.config", "passiveworkers.net.store",
              "passiveworkers.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "lb.db"))
    monkeypatch.setenv("PW_STARTER_CREDITS", "100")
    _reload()
    import passiveworkers.net.store as st
    return st.Store()


def _acct(store, owner, *, quality_sum=0.0, quality_n=0, earned=0.0, helped=0, balance=None):
    store.ledger.open_account(owner)
    a = store.ledger.accounts[owner]
    a.quality_sum, a.quality_n = quality_sum, quality_n
    a.lifetime_earned, a.jobs_helped = earned, helped
    if balance is not None:
        a.balance = balance
    return a


def test_ranks_by_reputation(store):
    _acct(store, "lo", quality_sum=12, quality_n=2, helped=2)    # avg 6.0
    _acct(store, "hi", quality_sum=18, quality_n=2, helped=2)    # avg 9.0
    ops = store.leaderboard(sort="reputation")["operators"]
    assert [o["owner"] for o in ops] == ["hi", "lo"]
    assert ops[0]["reputation"] == 9.0 and ops[0]["ratings"] == 2


def test_reputation_board_excludes_unrated(store):
    _acct(store, "rated", quality_sum=8, quality_n=1, helped=1)
    _acct(store, "unrated", quality_sum=0, quality_n=0, helped=5)
    rep = [o["owner"] for o in store.leaderboard(sort="reputation")["operators"]]
    assert "unrated" not in rep and "rated" in rep
    helped = [o["owner"] for o in store.leaderboard(sort="helped")["operators"]]
    assert "unrated" in helped                                   # but appears on the helped board


def test_credits_uses_lifetime_earned_not_balance(store):
    _acct(store, "freeloader", earned=0.0, balance=10000)        # big unspent starter grant
    _acct(store, "worker", earned=250.0, balance=5)
    ops = store.leaderboard(sort="credits")["operators"]
    assert ops[0]["owner"] == "worker"                           # earned beats hoarded balance
    assert ops[0]["credits_earned"] == 250.0


def test_excludes_escrow(store):
    from passiveworkers.ledger import ESCROW_ID
    store.ledger._escrow()                                       # materialize the escrow account
    _acct(store, "alice", earned=5, helped=1)
    owners = [o["owner"] for o in store.leaderboard(sort="credits")["operators"]]
    assert ESCROW_ID not in owners


def test_is_pseudonymous_and_marks_online(store):
    # register_node opens the account AND marks the owner online (recent last_seen)
    store.register_node({"owner": "alice", "answer_model": "m", "country": "DE"}, geo_country="DE")
    a = store.ledger.accounts["alice"]
    a.quality_sum, a.quality_n = 9, 1
    ops = store.leaderboard(sort="reputation")["operators"]
    row = next(o for o in ops if o["owner"] == "alice")
    assert row["online"] is True and "DE" in row["countries"]
    for leaky in ("node_id", "ip", "source_ip", "secret", "machine_id"):
        assert leaky not in row


# ----------------------------------------------------------------- HTTP boundary (clamp + default)
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "lbhttp.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    _reload()
    import passiveworkers.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return TestClient(capp.app)


def test_endpoint_clamps_limit_and_defaults_bad_sort(client):
    import passiveworkers.net.coordinator_app as capp
    for i in range(150):                                         # enough that the cap actually bites
        capp.store.ledger.open_account(f"op{i:03d}")
        a = capp.store.ledger.accounts[f"op{i:03d}"]
        a.quality_sum, a.quality_n, a.jobs_helped = 9, 1, i      # all rated, so all rank
    j = client.get("/leaderboard?sort=bogus&limit=99999").json()
    assert j["sort"] == "reputation"                            # bad sort → default
    assert len(j["operators"]) == 100                          # high limit clamped to exactly 100
    assert len(client.get("/leaderboard?limit=0").json()["operators"]) == 1     # floored to >=1
    assert len(client.get("/leaderboard?limit=-5").json()["operators"]) == 1    # negative floored too
