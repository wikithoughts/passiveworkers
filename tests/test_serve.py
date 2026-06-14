"""The single-player web desk (council/serve.py): report listing/reading, the path-traversal guard
on /report/{name}, progress 404, and job spawn (research mocked — no Ollama)."""
import time

import pytest
from fastapi import HTTPException

import council.serve as serve


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(serve, "REPORTS", d)
    return d


@pytest.fixture()
def client(reports):
    from fastapi.testclient import TestClient
    return TestClient(serve.app)


def test_report_guard_basenames_blocks_traversal(reports):
    # a secret OUTSIDE the reports dir; the guard must basename the name and never escape
    (reports.parent / "secret.md").write_text("TOP SECRET")
    (reports / "ok.md").write_text("OK report")
    assert serve.report("ok.md") == "OK report"             # normal read works
    with pytest.raises(HTTPException):                       # ../secret.md → basename 'secret.md' ∉ reports
        serve.report("../secret.md")
    with pytest.raises(HTTPException):                       # absolute path → basenamed too
        serve.report("/etc/passwd")
    with pytest.raises(HTTPException):                       # non-.md rejected
        serve.report("ok.txt")
    with pytest.raises(HTTPException):                       # missing
        serve.report("nope.md")


def test_reports_lists_md_files(client, reports):
    (reports / "2026-06-14-a.md").write_text("a")
    (reports / "data.txt").write_text("x")                  # not listed
    listed = client.get("/reports").json()
    assert "2026-06-14-a.md" in listed and "data.txt" not in listed


def test_progress_unknown_job_is_404(client):
    assert client.get("/progress/deadbeef00").status_code == 404


def test_research_spawns_job_and_reports_done(client, reports, monkeypatch):
    out = reports / "done.md"
    out.write_text("the result")
    monkeypatch.setattr(serve, "run_research", lambda *a, **k: out)   # no real Ollama
    jid = client.post("/research", json={"brief": "hello", "depth": "quick",
                                         "analysts": 1}).json()["job_id"]
    for _ in range(100):                                     # the worker runs in a daemon thread
        p = client.get(f"/progress/{jid}").json()
        if p["done"]:
            break
        time.sleep(0.02)
    assert p["done"] and p["file"] == "done.md" and p["error"] is None


def _drain(client, jid):
    for _ in range(100):
        p = client.get(f"/progress/{jid}").json()
        if p["done"]:
            return p
        time.sleep(0.02)
    return p


def test_research_records_generic_error_and_marks_done(client, reports, monkeypatch):
    monkeypatch.setattr(serve, "run_research",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    jid = client.post("/research", json={"brief": "x", "depth": "quick", "analysts": 1}).json()["job_id"]
    p = _drain(client, jid)
    assert p["done"] and p["file"] is None and "RuntimeError" in p["error"]


def test_research_systemexit_does_not_hang_the_job(client, reports, monkeypatch):
    # run_research raises SystemExit on first-run failures (Ollama down). SystemExit is a
    # BaseException — if _work caught only Exception the job would hang at done=False forever.
    def no_ollama(*a, **k):
        raise SystemExit("Can't reach Ollama — start it with `ollama serve`.")
    monkeypatch.setattr(serve, "run_research", no_ollama)
    jid = client.post("/research", json={"brief": "x", "depth": "quick", "analysts": 1}).json()["job_id"]
    p = _drain(client, jid)
    assert p["done"] and "ollama serve" in p["error"].lower()
