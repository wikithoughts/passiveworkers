"""Content-addressed chunked file artifacts (D22): round-trip + integrity + path safety."""
import os
import pathlib

import pytest

from passiveworkers import artifacts as A


def _make(tmp_path, nbytes):
    p = tmp_path / "blob.bin"
    p.write_bytes(os.urandom(nbytes))
    return p


def test_chunk_and_roundtrip(tmp_path):
    src = _make(tmp_path, 600_000)        # ~3 chunks
    man, blobs = A.chunk_file(str(src))
    assert len(man["chunks"]) == 3 and A.verify_manifest(man)
    out = A.reassemble(man, lambda h: blobs.get(h), str(tmp_path / "out"))
    assert pathlib.Path(out).read_bytes() == src.read_bytes()


def test_tamper_detected(tmp_path):
    src = _make(tmp_path, 300_000)
    man, blobs = A.chunk_file(str(src))
    blobs[man["chunks"][0]] = b"corrupted"
    with pytest.raises(ValueError):
        A.reassemble(man, lambda h: blobs.get(h), str(tmp_path / "o"))


def test_missing_chunk_detected(tmp_path):
    src = _make(tmp_path, 300_000)
    man, blobs = A.chunk_file(str(src))
    with pytest.raises(ValueError):
        A.reassemble(man, lambda h: None, str(tmp_path / "o"))


def test_doctored_manifest_rejected(tmp_path):
    src = _make(tmp_path, 600_000)
    man, _ = A.chunk_file(str(src))
    man["chunks"] = list(reversed(man["chunks"]))   # root no longer matches
    assert not A.verify_manifest(man)


def test_path_traversal_name_neutralized(tmp_path):
    src = _make(tmp_path, 100_000)
    man, blobs = A.chunk_file(str(src))
    man["name"] = "../../etc/evil"
    out = pathlib.Path(A.reassemble(man, lambda h: blobs.get(h), str(tmp_path / "safe")))
    assert out.name == "evil" and str(out).startswith(str((tmp_path / "safe").resolve()))


def test_oversize_file_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "MAX_FILE_BYTES", 1000)
    src = _make(tmp_path, 5000)
    with pytest.raises(ValueError):
        A.chunk_file(str(src))


def test_blob_store_hash_check(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DB", str(tmp_path / "b.db"))
    import importlib
    import passiveworkers.net.config as cfg
    importlib.reload(cfg)
    import passiveworkers.net.store as st
    importlib.reload(st)
    store = st.Store()
    import hashlib
    data = b"hello chunk"
    h = hashlib.sha256(data).hexdigest()
    assert store.put_blob("job1", h, data)["ok"]
    assert not store.put_blob("job1", "0" * 64, data)["ok"]   # wrong hash rejected
    assert store.get_blob("job1", h) == data
    # composite (hash, job_id) PK: the SAME content under a second job is its own copy —
    # cross-job dedup must never strand the second asker (D22 review fix)
    assert store.put_blob("job2", h, data)["ok"]
    assert store.get_blob("job2", h) == data
    assert store.get_blob("never-uploaded", h) is None        # a job that didn't upload sees nothing
    # blobs_present gates payment on full upload
    assert store.blobs_present("job1", [h]) and not store.blobs_present("job1", ["0" * 64])


def test_artifact_discriminator_roundtrip():
    man = {"name": "x.bin", "size": 1, "chunks": ["a" * 64], "root": "r"}
    wrapped = A.wrap_artifact(man)
    assert A.read_artifact(wrapped) == man
    # plain text — even JSON text — is not mistaken for an artifact
    assert A.read_artifact("just some text") is None
    assert A.read_artifact('{"hello": "world"}') is None


def test_empty_file_manifest_valid():
    man = {"name": "empty.txt", "size": 0, "chunks": [], "root": A.manifest_root([])}
    assert A.verify_manifest(man)


def test_manifest_rejects_bad_hashes_and_size():
    assert not A.verify_manifest({"name": "x", "size": 5, "chunks": ["nothex"], "root": "r"})
    assert not A.verify_manifest({"name": "x", "size": -1, "chunks": [], "root": "r"})


# --------------------------------------------------------- .part + running cap (review F19)
def _three_chunk_file(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CHUNK_SIZE", 4)
    src = tmp_path / "deliv.txt"
    src.write_bytes(b"aaaabbbbcccc")
    return A.chunk_file(str(src))


def test_corrupt_chunk_leaves_no_partial_file(tmp_path, monkeypatch):
    man, blobs = _three_chunk_file(tmp_path, monkeypatch)
    bad = dict(blobs)
    bad[man["chunks"][2]] = b"XXXX"                       # last chunk corrupted
    out = tmp_path / "o"
    with pytest.raises(ValueError):
        A.reassemble(man, lambda h: bad.get(h), str(out))
    assert list(out.iterdir()) == []                     # no deliv.txt, no leftover .part


def test_failed_reassembly_keeps_an_existing_file(tmp_path, monkeypatch):
    man, blobs = _three_chunk_file(tmp_path, monkeypatch)
    out = tmp_path / "o"
    out.mkdir()
    (out / "deliv.txt").write_bytes(b"earlier good copy")
    with pytest.raises(ValueError):
        A.reassemble(man, lambda h: None if h == man["chunks"][1] else blobs.get(h), str(out))
    assert (out / "deliv.txt").read_bytes() == b"earlier good copy"
    assert [p.name for p in out.iterdir()] == ["deliv.txt"]
    # and a good reassembly then replaces it atomically
    assert A.reassemble(man, lambda h: blobs.get(h), str(out)).read_bytes() == b"aaaabbbbcccc"
    assert [p.name for p in out.iterdir()] == ["deliv.txt"]


def test_running_cap_stops_at_declared_size(tmp_path, monkeypatch):
    man, blobs = _three_chunk_file(tmp_path, monkeypatch)
    man = dict(man, size=5)                               # lies: 12 bytes of chunks
    fetched = []

    def fetch(h):
        fetched.append(h)
        return blobs.get(h)
    with pytest.raises(ValueError):
        A.reassemble(man, fetch, str(tmp_path / "o"))
    assert len(fetched) == 2                              # aborted once past 5 bytes, not after all 3
    assert list((tmp_path / "o").iterdir()) == []


def test_declared_size_over_cap_rejected_before_fetching(tmp_path, monkeypatch):
    man, blobs = _three_chunk_file(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "MAX_FILE_BYTES", 8)
    fetched = []
    with pytest.raises(ValueError):
        A.reassemble(man, lambda h: fetched.append(h) or blobs.get(h), str(tmp_path / "o"))
    assert fetched == []
