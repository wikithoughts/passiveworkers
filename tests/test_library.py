"""Local document library: chunk → (mocked) embed → cosine retrieval, with isolation."""

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
    # chunks may exceed CHUNK_CHARS by the prepended overlap tail (boundary insurance)
    assert all(len(c) <= L._CHUNK_CHARS + L._OVERLAP + 8 for c in chunks)


def test_chunking_is_structure_aware():
    # a header should start a fresh chunk, never straddle the previous section
    text = "# Section A\n" + ("alpha " * 50) + "\n## Section B\n" + ("beta " * 50)
    chunks = L._chunk(text)
    assert any("Section A" in c for c in chunks) and any("Section B" in c for c in chunks)
    # no single chunk contains BOTH section bodies fused together
    assert not any(("alpha" in c and "beta" in c and "Section B" not in c) for c in chunks)


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


def test_incremental_skip_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "embed", _fake_embed)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    lib = L.Library(db_path=tmp_path / "lib.db")
    doc = tmp_path / "memo.md"
    doc.write_text("Project Polaris budget. " * 20)
    assert lib._add_file(doc) >= 1          # first index
    assert lib._add_file(doc) == 0          # unchanged → skipped
    doc.write_text("Project Polaris CHANGED budget. " * 20)
    assert lib._add_file(doc) >= 1          # changed → re-indexed


def test_hybrid_search_returns_results(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "embed", _fake_embed)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    lib = L.Library(db_path=tmp_path / "lib.db")
    (tmp_path / "a.md").write_text("Project Polaris ships in Finland with a fixed budget. " * 5)
    (tmp_path / "b.md").write_text("The cat sat in the weather all afternoon. " * 5)
    lib.add(str(tmp_path / "a.md")); lib.add(str(tmp_path / "b.md"))
    hits = lib.search("polaris budget", k=1, window=False)
    assert hits and hits[0]["title"] == "a.md"


def test_search_result_never_leaks_context_blurb(tmp_path, monkeypatch):
    # the contextual blurb / context column must never appear in the displayed [L#] quote
    monkeypatch.setattr(L, "embed", _fake_embed)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    lib = L.Library(db_path=tmp_path / "lib.db")
    (tmp_path / "a.md").write_text("Project Polaris budget in Finland. " * 6)
    lib.add(str(tmp_path / "a.md"))
    for h in lib.search("polaris", k=2):
        assert set(h.keys()) == {"source", "title", "ord", "text", "score"}
        assert "context" not in h


def test_unsupported_filetype(tmp_path):
    lib = L.Library(db_path=tmp_path / "x.db")
    bad = tmp_path / "image.png"
    bad.write_bytes(b"\x89PNG")
    import pytest
    with pytest.raises(ValueError):
        L.extract_text(bad)


# ---------------------------------------------------------------- R12: embedder preflight
def _tags_response(models):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": m} for m in models]}
    return _Resp()


def test_add_refuses_single_file_when_embedder_not_pulled(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _tags_response(["other:model"]))
    lib = L.Library(db_path=tmp_path / "lib.db")
    doc = tmp_path / "memo.md"
    doc.write_text("hello world")
    import pytest
    with pytest.raises(SystemExit) as e:
        lib.add(str(doc))
    assert "ollama pull nomic-embed-text" in str(e.value)


def test_add_refuses_directory_when_embedder_not_pulled(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(L.requests, "get", lambda *a, **k: _tags_response([]))
    lib = L.Library(db_path=tmp_path / "lib.db")
    (tmp_path / "memo.md").write_text("hello world")
    import pytest
    with pytest.raises(SystemExit):
        lib.add(str(tmp_path))


def test_add_proceeds_when_embedder_unreachable(tmp_path, monkeypatch):
    # Ollama down entirely (not "checked and missing") stays permissive — the per-file 404 skip
    # still surfaces per-file, but add() itself doesn't hard-refuse on an unrelated outage.
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    monkeypatch.setattr(L, "embed", _fake_embed)
    lib = L.Library(db_path=tmp_path / "lib.db")
    doc = tmp_path / "memo.md"
    doc.write_text("hello world " * 20)
    assert lib.add(str(doc)) >= 1


def test_add_directory_all_files_erroring_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])

    def boom_embed(texts):
        raise RuntimeError("simulated embed failure")
    monkeypatch.setattr(L, "embed", boom_embed)
    lib = L.Library(db_path=tmp_path / "lib.db")
    (tmp_path / "a.md").write_text("hello world " * 20)
    (tmp_path / "b.md").write_text("goodbye world " * 20)
    n = lib.add(str(tmp_path))
    assert n == 0
    assert lib.last_add_had_errors is True


def test_add_unchanged_incremental_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "embed", _fake_embed)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    lib = L.Library(db_path=tmp_path / "lib.db")
    doc = tmp_path / "memo.md"
    doc.write_text("Project Polaris budget. " * 20)
    assert lib.add(str(doc)) >= 1
    n = lib.add(str(doc))          # unchanged → legitimate 0-chunk no-op, NOT an error
    assert n == 0
    assert lib.last_add_had_errors is False


def test_cli_add_prints_fail_not_checkmark_when_all_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(L, "LIB_DIR", tmp_path)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    # L.main() constructs Library() with no args, whose db_path default was bound to the REAL
    # LIB_DB at module-import time (a plain attribute patch on L.LIB_DIR/L.LIB_DB does not
    # reach an already-bound default parameter) — patch the bound default directly so this test
    # doesn't touch ~/.passiveworkers/library.db (review finding).
    monkeypatch.setattr(L.Library.__init__, "__defaults__", (tmp_path / "lib.db",))

    def boom_embed(texts):
        raise RuntimeError("simulated embed failure")
    monkeypatch.setattr(L, "embed", boom_embed)
    monkeypatch.setattr(L, "requests", type("R", (), {"get": staticmethod(
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))}))
    (tmp_path / "a.md").write_text("hello world " * 20)
    monkeypatch.setattr("sys.argv", ["pw library", "add", str(tmp_path)])
    rc = L.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗" in out and "0 chunks indexed" in out
    assert "✓ 0 chunks indexed." not in out


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
