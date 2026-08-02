"""council/net/agent.py — Agent.run()'s full poll -> claim -> execute -> deliver loop, driven
against a REAL coordinator (via the requests->TestClient shim in conftest.py), not the
monkeypatched-away version tests/test_join.py uses. R31 review: this loop is the code a
stranger's machine runs 24/7 under `pw join`/`pw work`, and it previously had zero coverage."""
from __future__ import annotations

import requests as _real_requests

import council.net.agent as A


def _wired_shim(coord_client, shim_factory):
    """The shim swaps out the `requests` NAME entirely, but council.net.agent._fix_hint checks
    `requests.exceptions.ConnectionError` for its isinstance branch — expose the real exceptions
    module on the shim (the same convention tests/test_join.py's _FakeRequests already uses)."""
    shim = shim_factory(coord_client, "http://coord")
    shim.exceptions = _real_requests.exceptions
    return shim


def test_agent_run_drains_answer_and_judge_tasks_via_real_coordinator(
    coord_client, shim_factory, monkeypatch
):
    monkeypatch.setattr(A, "requests", _wired_shim(coord_client, shim_factory))
    monkeypatch.setattr(A.Agent, "_heartbeat_loop", lambda self: None)   # isolate the poll loop
    monkeypatch.setattr("council.ollama.generate", lambda *a, **k: ("a canned answer", 42))

    monkeypatch.setenv("PW_COORDINATOR", "http://coord")
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_ANSWER_MODEL", "test-model")
    # A lone operator must judge by default (D47) so one node can complete BOTH the answer and
    # judge stages of a minds=1 job.
    monkeypatch.setenv("PW_CAN_JUDGE", "1")

    agent = A.Agent()
    agent.register()   # a job needs an ONLINE node to assign to at creation time

    asker = coord_client.post("/users", json={"handle": "alice"}).json()["user_secret"]
    job = coord_client.post("/jobs", json={"question": "test brief", "type": "chat", "minds": 1},
                            headers={"X-User-Secret": asker}).json()
    assert job["status"] == "pending_answers"

    # run()'s while loop only sleeps on "nothing to do yet" branches (RequestException / 401 /
    # 204-empty) — never mid-task — so making the mocked sleep stop the loop on first call drains
    # every currently-queued task (answer, then judge) before halting cleanly.
    def fake_sleep(_s):
        agent._running = False
    monkeypatch.setattr(A.time, "sleep", fake_sleep)

    agent.run()

    view = coord_client.get(f"/jobs/{job['job_id']}").json()
    assert view["status"] == "done"
    assert view.get("merged")
    assert "a canned answer" in view["merged"]
    assert agent._tasks_ok >= 1
    assert agent._tasks_failed == 0


def test_agent_run_reports_task_failure_via_fix_hint_and_counters(
    coord_client, shim_factory, monkeypatch, capsys
):
    monkeypatch.setattr(A, "requests", _wired_shim(coord_client, shim_factory))
    monkeypatch.setattr(A.Agent, "_heartbeat_loop", lambda self: None)

    def boom_generate(*a, **k):
        raise ConnectionError("Connection refused")
    monkeypatch.setattr("council.ollama.generate", boom_generate)

    monkeypatch.setenv("PW_COORDINATOR", "http://coord")
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_ANSWER_MODEL", "test-model")
    monkeypatch.setenv("PW_CAN_JUDGE", "1")

    agent = A.Agent()
    agent.register()

    asker = coord_client.post("/users", json={"handle": "bob"}).json()["user_secret"]
    job = coord_client.post("/jobs", json={"question": "test brief", "type": "chat", "minds": 1},
                            headers={"X-User-Secret": asker}).json()

    calls = {"n": 0}

    def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] >= 3:      # bound the loop even if it keeps finding failed-then-retried work
            agent._running = False
    monkeypatch.setattr(A.time, "sleep", fake_sleep)

    agent.run()

    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "is it running" in out   # _fix_hint mapped the ConnectionError, not a raw traceback
    assert agent._tasks_failed >= 1

    view = coord_client.get(f"/jobs/{job['job_id']}").json()
    assert view["status"] == "failed"
