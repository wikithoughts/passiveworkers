"""council/operator.py — the composed `pw fetch` path (R31 review): signature verification, the
encryption downgrade guard, and content-addressed chunk reassembly, all driven through the REAL
coordinator via the requests->TestClient shim. tests/test_artifacts.py and tests/test_trust.py
already cover these primitives in isolation; this covers the COMPOSITION, which contains its own
security logic (operator.py:_verify_delivery_signature + the encrypt_to downgrade check) and had
zero coverage before this."""
from __future__ import annotations

import importlib

import pytest

BASE = "http://coord"


@pytest.fixture
def op_module(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_LIBRARY_DIR", str(tmp_path))
    import council.operator as OP
    return importlib.reload(OP)


def _wire(OP, coord_client, shim_factory, monkeypatch, owner="bob"):
    monkeypatch.setattr(OP, "requests", shim_factory(coord_client, BASE))
    monkeypatch.setenv("PW_COORDINATOR", BASE)
    monkeypatch.setenv("PW_TOKEN", "tok")
    monkeypatch.setenv("PW_OWNER", owner)
    monkeypatch.setenv("PW_OLLAMA_BASE", BASE)   # _profile probes BASE/api/tags -> 404 -> models=[]


def test_fetch_composition_rejects_downgrade_and_verifies_real_delivery(
    coord_client, op_module, shim_factory, monkeypatch, capsys, tmp_path
):
    from council import crypto as C
    if not C.available():
        pytest.skip("crypto extra not installed")
    OP = op_module

    # --- asker side: mint a box keypair, require encryption on the job ---
    asker_secret = coord_client.post("/users", json={"handle": "alice"}).json()["user_secret"]
    monkeypatch.setenv("PW_USER_SECRET", asker_secret)
    assert OP.keygen() == 0
    asker_pub = capsys.readouterr().out.strip().splitlines()[-1]

    # ================= (a) encryption downgrade guard =================
    job1 = coord_client.post(
        "/jobs",
        json={"question": "task one", "type": "assisted", "encrypt_to": asker_pub},
        headers={"X-User-Secret": asker_secret},
    ).json()
    job1_id = job1["job_id"]

    _wire(OP, coord_client, shim_factory, monkeypatch, owner="bob")
    op1 = OP.Operator()   # registers node "bob"
    offers = coord_client.get("/tasks/offers", headers=op1._headers()).json()["offers"]
    tid1 = next(o["task_id"] for o in offers if True)
    assert op1.accept(tid1) == 0

    # Bypass Operator.deliver() entirely — submit a PLAINTEXT, UNSIGNED delivery directly against
    # the coordinator, simulating a malicious/misbehaving operator (or a hostile coordinator
    # relaying one) trying to ship an unencrypted deliverable for a job that required encryption.
    src = tmp_path / "secret.txt"
    src.write_bytes(b"plaintext contents that should never reach the asker unencrypted")
    from council import artifacts as A
    manifest, blobs = A.chunk_file(str(src))
    for h, buf in blobs.items():
        rb = coord_client.post(f"/jobs/{job1_id}/blobs/{h}", headers=op1._headers(), content=buf)
        assert rb.status_code == 200
    r = coord_client.post(f"/tasks/{tid1}/deliver", headers=op1._headers(),
                          json={"deliverable": A.wrap_artifact(manifest),
                                "signature": "", "signer_pub": ""})
    assert r.status_code == 200   # the coordinator itself doesn't enforce encryption — the ASKER does

    monkeypatch.setenv("PW_USER_SECRET", asker_secret)
    rc = OP.fetch(job1_id, str(tmp_path / "out1"))
    assert rc == 1
    assert "required encryption" in capsys.readouterr().out.lower()
    assert not (tmp_path / "out1").exists() or not any((tmp_path / "out1").iterdir())

    # ================= (b) real signed + encrypted delivery, verified + reassembled =================
    job2 = coord_client.post(
        "/jobs",
        json={"question": "task two", "type": "assisted", "encrypt_to": asker_pub},
        headers={"X-User-Secret": asker_secret},
    ).json()
    job2_id = job2["job_id"]

    offers = coord_client.get("/tasks/offers", headers=op1._headers()).json()["offers"]
    tid2 = next(o["task_id"] for o in offers if o["task_id"] != tid1)
    assert op1.accept(tid2) == 0

    src2 = tmp_path / "real_deliverable.txt"
    payload = b"the operator's real, owned, encrypted deliverable"
    src2.write_bytes(payload)
    assert op1.deliver(tid2, f"@{src2}", job2_id) == 0   # real path: signs + encrypts

    monkeypatch.setenv("PW_USER_SECRET", asker_secret)
    rc = OP.fetch(job2_id, str(tmp_path / "out2"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "signature: ✓ valid" in out   # TOFU first-contact pin path
    reassembled = (tmp_path / "out2" / "real_deliverable.txt")
    assert reassembled.exists()
    assert reassembled.read_bytes() == payload   # decrypted + hash-verified chunk reassembly
