"""Local document library: chunk → (mocked) embed → cosine retrieval, with isolation."""

import pytest

import passiveworkers.library as L


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
    monkeypatch.setattr("sys.argv", ["pworkers library", "add", str(tmp_path)])
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


# --------------------------------------------------------- re-index safety + shared corpus cache (F20)
def _lib(tmp_path, monkeypatch, embed=_fake_embed):
    monkeypatch.setattr(L, "embed", embed)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    L._CORPUS_CACHE.clear()
    return L.Library(db_path=tmp_path / "lib.db")


def test_failed_embed_on_reindex_keeps_the_old_chunks(tmp_path, monkeypatch):
    lib = _lib(tmp_path, monkeypatch)
    doc, other = tmp_path / "memo.md", tmp_path / "other.md"
    doc.write_text("Project Polaris ships in Finland. " * 5)
    other.write_text("The cat sat in the weather. " * 5)
    assert lib._add_file(doc) >= 1
    doc.write_text("Project Polaris CHANGED its budget. " * 5)

    def boom(texts):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(L, "embed", boom)
    with pytest.raises(RuntimeError):
        lib._add_file(doc)
    monkeypatch.setattr(L, "embed", _fake_embed)
    assert lib._add_file(other) >= 1        # the next file's commit used to make the DELETE stick
    texts = [r["text"] for r in lib.conn.execute("SELECT text FROM chunks WHERE source=?",
                                                 (str(doc),))]
    assert texts and all("ships in Finland" in t for t in texts)
    assert any(h["title"] == "memo.md" for h in lib.search("polaris", k=2, window=False))


def _count_bm25_builds(monkeypatch):
    import passiveworkers.retrieval as RT
    builds = []

    class Counting(RT.BM25Okapi):
        def __init__(self, corpus, *a, **k):
            builds.append(len(corpus))
            super().__init__(corpus, *a, **k)
    monkeypatch.setattr(RT, "BM25Okapi", Counting)
    return builds


def test_corpus_cache_is_shared_across_library_instances(tmp_path, monkeypatch):
    lib = _lib(tmp_path, monkeypatch)
    (tmp_path / "a.md").write_text("Project Polaris ships in Finland. " * 5)
    lib.add(str(tmp_path / "a.md"))
    builds = _count_bm25_builds(monkeypatch)
    for _ in range(3):   # the researcher opens a fresh Library() per analyst call
        assert L.Library(db_path=tmp_path / "lib.db").search("polaris", k=1)
    assert len(builds) == 1


def test_corpus_cache_invalidated_by_a_reindex_that_reuses_ids(tmp_path, monkeypatch):
    lib = _lib(tmp_path, monkeypatch)
    doc = tmp_path / "a.md"
    doc.write_text("Project Polaris ships in Finland. " * 5)
    lib.add(str(doc))
    assert "Finland" in lib.search("polaris", k=1, window=False)[0]["text"]
    # same chunk count → SQLite reuses the freed rowids, so count+max(id) alone can't see it
    doc.write_text("Project Polaris moved to Norway. " * 5)
    L.Library(db_path=tmp_path / "lib.db").add(str(doc))
    assert "Norway" in lib.search("polaris", k=1, window=False)[0]["text"]


def test_corpus_cache_sees_writes_from_another_connection(tmp_path, monkeypatch):
    import os
    import sqlite3
    lib = _lib(tmp_path, monkeypatch)
    (tmp_path / "a.md").write_text("Project Polaris ships in Finland. " * 5)
    lib.add(str(tmp_path / "a.md"))
    assert "Finland" in lib.search("polaris", k=1, window=False)[0]["text"]
    db = tmp_path / "lib.db"
    c = sqlite3.connect(str(db))        # e.g. `pworkers library add` in another process
    c.execute("UPDATE chunks SET text=replace(text, 'Finland', 'Norway')")
    c.commit()
    c.close()
    st = os.stat(db)                    # pin a distinct mtime (coarse FS clocks can repeat one)
    os.utime(db, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert "Norway" in lib.search("polaris", k=1, window=False)[0]["text"]


# --------------------------------------------------------- researcher retry trims local (minor)
def test_researcher_retry_trims_local_sources_it_drafted_from(monkeypatch):
    import passiveworkers.researcher as RW

    class FakeLibrary:
        def search(self, query, k=5):
            return [{"title": f"doc{i}.md", "source": f"/d/doc{i}.md", "text": f"text {i}"}
                    for i in range(6)]
    monkeypatch.setattr(L, "Library", FakeLibrary)
    seen = []

    def draft(self, brief, evidence, local=None):
        if not seen:
            seen.append(None)
            raise RuntimeError("model timed out")
        seen.append(len(local or []))
        return "finding [L1].", 3
    monkeypatch.setattr(RW.ResearchWorker, "_plan_queries", lambda self, brief: ["q"])
    monkeypatch.setattr(RW.ResearchWorker, "_refine_queries", lambda self, brief, ev: [])
    monkeypatch.setattr(RW.ResearchWorker, "_draft", draft)
    monkeypatch.setattr(RW.ResearchWorker, "_repair_draft", lambda self, brief, d, ev, loc: d)
    for k in ("PW_RESEARCH_MAX_ROUNDS", "PW_RESEARCH_DEADLINE", "PW_RESEARCH_MAX_SOURCES"):
        monkeypatch.delenv(k, raising=False)
    w = RW.ResearchWorker(worker_id="m", model="m", depth="standard", scope="local")
    out = w.research("a stable brief about basic chemistry facts")
    assert seen == [None, 4]                                  # the retry drafted from 4 documents
    assert len(out["research"]["local_sources"]) == 4         # …so only those 4 are listed (was 6)
    assert "doc4.md" not in out["text"]


def test_arabic_query_finds_arabic_document(tmp_path, monkeypatch):
    # F10: BM25 used to tokenize Arabic to nothing, so an Arabic library was unsearchable lexically.
    # The embedder below ranks the target document only 2nd of 3 (dense), so it can only come out
    # on top if BM25 ranks it 1st — which needs Arabic tokens.
    def embed(texts):
        def v(t):
            if "الطقس" in t:
                return [1.0, 1.0]     # weather: least similar to the query
            if "الذكاء" in t and "التعليم" in t:
                return [1.0, 0.5]     # the target document: middle
            if "الرياضة" in t:
                return [1.0, 0.1]     # sports: most similar (dense alone would pick it)
            return [1.0, 0.0]         # the query
        return [v(t) for t in texts]
    monkeypatch.setattr(L, "embed", embed)
    monkeypatch.setattr(L, "_ROOTS", [tmp_path.resolve()])
    lib = L.Library(db_path=tmp_path / "lib.db")
    (tmp_path / "weather.md").write_text("الطقس جميل اليوم في المدينة والسماء صافية. " * 5)
    (tmp_path / "ai.md").write_text("الذكاء الاصطناعي يغير طريقة التعليم في المدارس. " * 5)
    (tmp_path / "sports.md").write_text("الرياضة مفيدة للصحة وكرة القدم شعبية جدا. " * 5)
    for f in ("weather.md", "ai.md", "sports.md"):
        lib.add(str(tmp_path / f))
    hits = lib.search("الذكاء الاصطناعي", k=1, window=False)
    assert hits and hits[0]["title"] == "ai.md"
