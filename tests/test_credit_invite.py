"""council/operator.py — `pw credit` and `pw invite` (R33 review): a worker's own balance
without needing the public /leaderboard, and a real CLI wrapper over /admin/enroll instead of a
raw curl. Network verbs run through the real coordinator via the requests->TestClient shim."""
import importlib
import json

import pytest

BASE = "http://coord"


@pytest.fixture
def op_module(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    import council.operator as OP
    return importlib.reload(OP)


def _wire(OP, coord_client, shim_factory, monkeypatch):
    monkeypatch.setattr(OP, "requests", shim_factory(coord_client, BASE))
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.setenv("PW_TOKEN", "tok")


# --------------------------------------------------------------------- pw credit
def test_credit_reads_joined_identity(coord_client, op_module, shim_factory, monkeypatch, capsys):
    _wire(op_module, coord_client, shim_factory, monkeypatch)
    reg = coord_client.post("/nodes/register",
                            json={"owner": "alice", "answer_model": "m"},
                            headers={"X-PW-Token": "tok"}).json()
    from council.net.agent import _save_join
    _save_join({"default": BASE, BASE: {"owner": "alice", "node_id": reg["node_id"],
                                        "node_secret": reg["node_secret"]}})

    assert op_module.credit() == 0
    out = capsys.readouterr().out
    assert "balance:" in out and "reputation:" in out and "jobs helped:" in out


def test_credit_falls_back_to_operator_json_identity(coord_client, op_module, shim_factory,
                                                      monkeypatch, capsys):
    _wire(op_module, coord_client, shim_factory, monkeypatch)
    monkeypatch.setenv("PW_OWNER", "bob")
    monkeypatch.setenv("PW_OLLAMA_BASE", BASE)   # _profile probes BASE/api/tags -> 404 -> models=[]
    op_module.Operator()   # registers + caches identity into operator.json (no join.json entry)

    assert op_module.credit() == 0
    assert "balance:" in capsys.readouterr().out


def test_credit_no_identity_anywhere_prints_hint_and_exits_2(coord_client, op_module,
                                                              shim_factory, monkeypatch, capsys):
    _wire(op_module, coord_client, shim_factory, monkeypatch)
    assert op_module.credit() == 2
    assert "pw join" in capsys.readouterr().out


def test_credit_prints_error_not_traceback_on_network_failure(op_module, monkeypatch, capsys):
    import requests
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    from council.net.agent import _save_join
    _save_join({"default": BASE, BASE: {"owner": "alice", "node_secret": "sec"}})
    monkeypatch.setattr(op_module, "requests", type("R", (), {
        "RequestException": requests.RequestException,
        "get": staticmethod(lambda *a, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("refused")))}))
    assert op_module.credit() == 1
    assert "✗" in capsys.readouterr().out


def test_credit_requires_coordinator(op_module, monkeypatch, capsys):
    monkeypatch.delenv("PW_COORDINATOR", raising=False)
    assert op_module.credit() == 2
    assert "PW_COORDINATOR" in capsys.readouterr().out


# --------------------------------------------------------------------- pw invite
def test_invite_mints_token_and_prints_join_and_ask_commands(coord_client, op_module,
                                                              shim_factory, monkeypatch, capsys):
    _wire(op_module, coord_client, shim_factory, monkeypatch)
    rc = op_module.invite(["--owner", "carol", "--kind", "node"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "enrollment token minted" in out
    assert "pw join" in out and "pw ask" in out


def test_invite_requires_admin_token(op_module, monkeypatch, capsys):
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.delenv("PW_TOKEN", raising=False)
    assert op_module.invite([]) == 2
    assert "PW_TOKEN" in capsys.readouterr().out


def test_invite_prints_error_not_traceback_on_network_failure(op_module, monkeypatch, capsys):
    import requests
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setattr(op_module, "requests", type("R", (), {
        "RequestException": requests.RequestException,
        "post": staticmethod(lambda *a, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("refused")))}))
    assert op_module.invite([]) == 1
    assert "✗" in capsys.readouterr().out


def test_invite_surfaces_403_from_bad_admin_token(coord_client, op_module, shim_factory,
                                                  monkeypatch, capsys):
    monkeypatch.setattr(op_module, "requests", shim_factory(coord_client, BASE))
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.setenv("PW_TOKEN", "wrong-token")
    rc = op_module.invite([])
    assert rc == 1
    assert "✗" in capsys.readouterr().out


# --------------------------------------------------------------------- joint smoke test (R23+R33)
def test_invite_then_ask_with_enroll_token_gets_starter_grant(coord_client, shim_factory,
                                                              monkeypatch, tmp_path):
    monkeypatch.setenv("PW_ENROLL", "1")   # _enroll_enabled() reads this at call time
    r = coord_client.post("/admin/enroll", json={"owner": "", "kind": "user", "max_uses": 1},
                          headers={"X-PW-Token": "tok"})
    assert r.status_code == 200
    token = r.json()["enroll_token"]

    import council.net.submit as S
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.delenv("PW_USER_SECRET", raising=False)
    monkeypatch.setattr(S, "requests", shim_factory(coord_client, BASE))
    monkeypatch.setattr("sys.argv", ["pw ask", "hello?", "--asker", "dave",
                                    "--enroll-token", token])
    S.ask_main()

    state = json.loads((tmp_path / "asker.json").read_text())
    secret = state[BASE]["user_secret"]
    bal = coord_client.get("/me", headers={"X-User-Secret": secret}).json()
    assert bal["balance"] > 0   # enrollment-gated signup got a real starter grant, not zero
