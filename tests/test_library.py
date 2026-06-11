"""Local document library: chunk → (mocked) embed → cosine retrieval, with isolation."""
import pathlib

import council.library as L


def _fake_embed(texts):
    # deterministic toy embedding: bag-of-keywords over a tiny vocab → cosine still meaningful
    vocab = ["polaris", "budget", "finland", "cat", "weather"]
    out = []
    for t in texts:
        tl = t.lower()
        out.append([float(tl.count(w)) + 0.01 for w in vocab])
    return out


def test_chunking_overlap():
    chunks = L._chunk("word " * 1000)
    assert len(chunks) >= 2
    assert all(len(c) <= L._CHUNK_CHARS for c in chunks)


def test_add_and_search(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "embed", _fake_embed)
    monkeypatch.setattr(L, "LIB_DIR", tmp_path)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    lib = L.Library(db_path=tmp_path / "lib.db")
    doc = tmp_path / "memo.md"
    doc.write_text("Project Polaris ships in Finland. The budget is fixed. " * 5)
    other = tmp_path / "other.md"
    other.write_text("The cat sat in the weather. " * 5)
    assert lib.add(str(doc)) >= 1
    assert lib.add(str(other)) >= 1
    hits = lib.search("polaris budget", k=1)
    assert hits and "Polaris" in hits[0]["text"]
    assert hits[0]["title"] == "memo.md"


def test_empty_library_returns_no_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "embed", _fake_embed)
    lib = L.Library(db_path=tmp_path / "empty.db")
    assert lib.is_empty()
    assert lib.search("anything") == []


def test_unsupported_filetype(tmp_path):
    lib = L.Library(db_path=tmp_path / "x.db")
    bad = tmp_path / "image.png"
    bad.write_bytes(b"\x89PNG")
    import pytest
    with pytest.raises(ValueError):
        L.extract_text(bad)


def test_path_confinement_rejects_outside_roots(tmp_path, monkeypatch):
    # confine roots to tmp_path; a file outside it must be rejected (MCP exfil guard)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(L, "embed", _fake_embed)
    lib = L.Library(db_path=tmp_path / "lib.db")
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret data")
    import pytest
    with pytest.raises(ValueError):
        lib.add(str(outside))
    # inside the root is fine
    inside = tmp_path / "ok.md"
    inside.write_text("hello " * 20)
    assert lib.add(str(inside)) >= 1
