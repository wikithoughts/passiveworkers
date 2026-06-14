"""D43 — offline, no-egress GeoIP on registration. The geoip module never raises and never dials out;
when no DB is configured it returns "" and the coordinator falls back to self-reported country
(default-off, behavior unchanged). When a country IS resolvable, /status surfaces geo_country +
geo_mismatch but NEVER the raw source IP."""
import importlib

import pytest

import council.net.geoip as G


def _reload():
    for m in ("council.ledger", "council.net.config", "council.net.store",
              "council.net.coordinator_app"):
        importlib.reload(importlib.import_module(m))


# ----------------------------------------------------------------- the geoip module (pure, offline)
def test_no_db_means_unavailable_and_empty(monkeypatch):
    monkeypatch.delenv("PW_GEOIP_DB", raising=False)
    assert G.available() is False
    assert G.country_for_ip("8.8.8.8") == ""          # no DB → "", never raises


def test_missing_db_file_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("PW_GEOIP_DB", str(tmp_path / "does-not-exist.mmdb"))
    assert G.available() is False
    assert G.country_for_ip("8.8.8.8") == ""


def test_private_and_loopback_never_resolve(monkeypatch):
    # even if a DB were present, a tunneled/loopback peer must not be "located"
    monkeypatch.setattr(G, "available", lambda: True)
    monkeypatch.setattr(G, "_get_reader", lambda: pytest.fail("must not look up private IPs"))
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.9", "::1", "fe80::1", ""):
        assert G.country_for_ip(ip) == ""


def test_garbage_ip_never_raises(monkeypatch):
    monkeypatch.setenv("PW_GEOIP_DB", "/nope")
    for bad in ("not-an-ip", "999.999.999.999", "", "   "):
        assert G.country_for_ip(bad) == ""


def test_public_ip_uses_reader_when_available(monkeypatch):
    monkeypatch.setattr(G, "available", lambda: True)

    class _C:
        class country:
            iso_code = "de"

    class _Reader:
        def country(self, ip):
            return _C()
    monkeypatch.setattr(G, "_get_reader", lambda: _Reader())
    assert G.country_for_ip("8.8.8.8") == "DE"         # upper-cased ISO2


def test_reader_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(G, "available", lambda: True)
    monkeypatch.setattr(G, "_get_reader",
                        lambda: (_ for _ in ()).throw(RuntimeError("corrupt db")))
    assert G.country_for_ip("8.8.8.8") == ""


# ----------------------------------------------------------------- register → /status integration
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "geo.db"))
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.delenv("PW_ENROLL", raising=False)        # open registration for these tests
    _reload()
    import council.net.coordinator_app as capp
    from fastapi.testclient import TestClient
    return capp, TestClient(capp.app)


def test_register_records_geo_and_status_flags_mismatch(client, monkeypatch):
    capp, c = client
    monkeypatch.setattr(capp.geoip, "country_for_ip", lambda ip: "DE")
    r = c.post("/nodes/register", json={"owner": "op", "answer_model": "m", "country": "BR"},
               headers={"X-PW-Token": "tok"})
    assert r.status_code == 200
    node = c.get("/status").json()["online_nodes"][0]
    assert node["geo_country"] == "DE"
    assert node["geo_mismatch"] is True                   # self-reported BR ≠ geo DE


def test_status_never_leaks_source_ip(client, monkeypatch):
    capp, c = client
    monkeypatch.setattr(capp.geoip, "country_for_ip", lambda ip: "DE")
    c.post("/nodes/register", json={"owner": "op", "answer_model": "m", "country": "DE"},
           headers={"X-PW-Token": "tok"})
    blob = c.get("/status").text.lower()
    assert "source_ip" not in blob and "\"ip\"" not in blob   # IPs stay server-side
    node = c.get("/status").json()["online_nodes"][0]
    assert node["geo_mismatch"] is False                  # DE == DE


def test_geo_unavailable_falls_back_to_self_reported(client, monkeypatch):
    capp, c = client
    monkeypatch.setattr(capp.geoip, "country_for_ip", lambda ip: "")   # default-off
    c.post("/nodes/register", json={"owner": "op", "answer_model": "m", "country": "BR"},
           headers={"X-PW-Token": "tok"})
    node = c.get("/status").json()["online_nodes"][0]
    assert node["geo_country"] == "" and node["geo_mismatch"] is False
    assert node["country"] == "BR"                         # unchanged behavior


def test_register_migration_adds_geo_column(tmp_path, monkeypatch):
    # an OLD nodes table (15 cols, no geo_country) must migrate cleanly + still register
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
      CREATE TABLE nodes(node_id TEXT PRIMARY KEY, name TEXT, country TEXT, owner TEXT,
        answer_model TEXT, lens TEXT, can_judge INT, judge_model TEXT, profile TEXT,
        last_seen REAL, load REAL, status TEXT, ip TEXT, secret_hash TEXT, machine_id TEXT);
    """)
    con.execute("INSERT INTO nodes(node_id, owner, country) VALUES('old','op','BR')")
    con.commit(); con.close()

    monkeypatch.setenv("PW_DB", str(db))
    _reload()
    import council.net.store as st
    s = st.Store()                                         # boots → runs the migration
    cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(nodes)")}
    assert "geo_country" in cols
    out = s.register_node({"owner": "op2", "answer_model": "m", "country": "DE"},
                          ip="8.8.8.8", geo_country="US")
    assert out["node_id"]
