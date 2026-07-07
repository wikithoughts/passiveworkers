"""council/operator.py — the operator + asker CLI client verbs (previously untested beyond the pure
_verify_delivery_signature helper). Network verbs run against a REAL coordinator through the
requests→TestClient shim (see conftest); local crypto/trust verbs run directly against a tmp home."""
import importlib

import pytest

BASE = "http://coord"


@pytest.fixture
def op_module(tmp_path, monkeypatch):
    """Fresh council.operator bound to a tmp PW_LIBRARY_DIR — its module-level STATE path (operator.json)
    is computed at import, so it must be reloaded AFTER the env is redirected, never touching the real home."""
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    import council.operator as OP
    return importlib.reload(OP)


def _wire(OP, coord_client, shim_factory, monkeypatch):
    monkeypatch.setattr(OP, "requests", shim_factory(coord_client, BASE))
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_OWNER", "bob")
    monkeypatch.setenv("PW_NAME", "bobbox")
    monkeypatch.setenv("PW_COUNTRY", "AE")
    monkeypatch.setenv("PW_OLLAMA_BASE", BASE)     # _profile probes BASE/api/tags → 404 → models=[]


# --------------------------------------------------------- network verbs (real coordinator via shim)
def test_assisted_flow_tasks_accept_deliver_rate(coord_client, op_module, shim_factory, monkeypatch, capsys):
    OP = op_module
    _wire(OP, coord_client, shim_factory, monkeypatch)

    # asker opens an assisted offer through the SAME client → same store the operator will hit
    asker = coord_client.post("/users", json={"handle": "alice"}).json()["user_secret"]
    job = coord_client.post("/jobs",
                            json={"question": "Design a minimal logo", "type": "assisted",
                                  "context": "blue, flat"},
                            headers={"X-User-Secret": asker}).json()
    assert job["status"] == "pending_assist"
    job_id = job["job_id"]

    op = OP.Operator()                              # registers node "bob" via the shim
    assert op.tasks() == 0
    assert "reward" in capsys.readouterr().out

    offers = coord_client.get("/tasks/offers", headers=op._headers()).json()["offers"]
    assert offers, "operator should see the open offer"
    tid = offers[0]["task_id"]

    assert op.accept(tid) == 0
    assert op.deliver(tid, "Here is the finished logo, described in text.") == 0
    assert "delivered" in capsys.readouterr().out

    monkeypatch.setenv("PW_USER_SECRET", asker)     # asker rates the delivery → operator reputation
    assert OP.rate(job_id, "8") == 0
    assert "reputation" in capsys.readouterr().out


def test_tasks_empty_when_no_offers(coord_client, op_module, shim_factory, monkeypatch, capsys):
    OP = op_module
    _wire(OP, coord_client, shim_factory, monkeypatch)
    assert OP.Operator().tasks() == 0
    assert "No open assisted offers" in capsys.readouterr().out


def test_accept_unknown_task_fails(coord_client, op_module, shim_factory, monkeypatch, capsys):
    OP = op_module
    _wire(OP, coord_client, shim_factory, monkeypatch)
    assert OP.Operator().accept("no-such-task") == 1
    assert "✗" in capsys.readouterr().out


def test_identity_is_cached_across_operators(coord_client, op_module, shim_factory, monkeypatch):
    OP = op_module
    _wire(OP, coord_client, shim_factory, monkeypatch)
    a = OP.Operator()
    b = OP.Operator()                               # second construction reuses the cached node identity
    assert (a.node_id, a.secret) == (b.node_id, b.secret)


# --------------------------------------------------------- asker-side rate guards (no coordinator)
def test_rate_requires_user_secret(op_module, monkeypatch, capsys):
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    monkeypatch.delenv("PW_USER_SECRET", raising=False)
    assert op_module.rate("job", "8") == 2
    assert "PW_USER_SECRET" in capsys.readouterr().out


def test_rate_bad_score(op_module, monkeypatch, capsys):
    monkeypatch.setenv("PW_COORDINATOR", "http://c")
    monkeypatch.setenv("PW_USER_SECRET", "SEC")
    assert op_module.rate("job", "notanumber") == 2
    assert "0-10" in capsys.readouterr().out


# --------------------------------------------------------- local crypto/trust verbs
def test_trust_add_list_remove(op_module, capsys):
    OP = op_module
    key = "aGVsbG8gd29ybGQgdGhpcyBpcyBhIGZha2Uga2V5IDEyMzQ="    # any b64-looking key; pin just stores+fingerprints
    assert OP.trust_cmd(["add", "carol", key]) == 0
    assert "pinned" in capsys.readouterr().out.lower()
    assert OP.trust_cmd(["list"]) == 0
    assert "carol" in capsys.readouterr().out
    assert OP.trust_cmd(["remove", "carol"]) == 0
    assert "unpinned" in capsys.readouterr().out
    assert OP.trust_cmd(["list"]) == 0
    assert "No pinned operators" in capsys.readouterr().out


def test_trust_usage_on_bad_args(op_module, capsys):
    assert op_module.trust_cmd([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_keygen_and_fingerprint(op_module, capsys):
    from council import crypto as C
    if not C.available():
        pytest.skip("crypto extra not installed")
    OP = op_module
    assert OP.keygen() == 0
    assert len(capsys.readouterr().out.strip().splitlines()[-1]) > 20    # printed a real key
    assert OP.fingerprint() == 0
    assert "Fingerprint" in capsys.readouterr().out
