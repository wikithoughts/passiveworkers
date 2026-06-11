#!/usr/bin/env python3
"""
council/library.py — your private document library (local RAG, D19)
====================================================================
Index your own files once; the research engine then draws on them ALONGSIDE the live web.
Everything is local and keyless: text is chunked, embedded with Ollama `nomic-embed-text`,
and stored in SQLite at ~/.passiveworkers/library.db. Nothing ever leaves your machine —
no cloud, no account, no telemetry. Retrieval is plain numpy cosine over the stored matrix
(no heavy vector DB), in keeping with the project's lean, auditable ethos.

CLI:
    pw library add <path|dir>      index a file or a whole directory
    pw library list                what's indexed
    pw library remove <path>       drop a document
    pw library clear               wipe the library

Used by council/researcher.py (the `[L#]` local citations) and the MCP server.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys

import requests

OLLAMA = os.environ.get("PW_OLLAMA_BASE", "http://localhost:11434")
EMBED_MODEL = os.environ.get("PW_EMBED_MODEL", "nomic-embed-text")
LIB_DIR = pathlib.Path(os.environ.get("PW_LIBRARY_DIR",
                                      str(pathlib.Path.home() / ".passiveworkers")))
LIB_DB = LIB_DIR / "library.db"
_CHUNK_CHARS = 2000          # ~512 tokens — the recall sweet spot (R8 research)
_OVERLAP = 256               # light boundary insurance; parent-window supplies real context
_CONTEXTUAL = os.environ.get("PW_CONTEXTUAL_CHUNKS", "") not in ("", "0", "false")
_CTX_MODEL = os.environ.get("PW_CONTEXT_MODEL", "")   # small chat model for situating blurbs
_RERANK = os.environ.get("PW_RERANK", "") not in ("", "0", "false")   # opt-in listwise rerank
_TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".csv", ".log"}
# Ingest guards (resource exhaustion): per-file size, file count, total bytes per add().
_MAX_FILE_BYTES = int(os.environ.get("PW_MAX_FILE_MB", "30")) * 1_000_000
_MAX_FILES = int(os.environ.get("PW_MAX_FILES", "500"))
_MAX_TOTAL_BYTES = int(os.environ.get("PW_MAX_TOTAL_MB", "300")) * 1_000_000
# Path confinement: indexing is restricted to these roots (default: your home dir). This
# stops an MCP-connected agent (or a stray path) from indexing system / other-user files.
# Narrow it to e.g. ~/Documents by setting PW_LIBRARY_ROOTS.
_ROOTS = [pathlib.Path(p).expanduser().resolve()
          for p in os.environ.get("PW_LIBRARY_ROOTS", str(pathlib.Path.home())).split(os.pathsep)
          if p.strip()]


def _within_roots(p: pathlib.Path) -> bool:
    try:
        rp = p.resolve()
    except Exception:
        return False
    return any(rp == r or rp.is_relative_to(r) for r in _ROOTS)


# ------------------------------------------------------------------ text extraction
def _read_text_file(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: pathlib.Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _read_docx(path: pathlib.Path) -> str:
    import docx
    return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)


def extract_text(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return _read_docx(path)
    if ext in _TEXT_EXT:
        return _read_text_file(path)
    raise ValueError(f"unsupported file type: {ext} (supported: pdf, docx, txt/md/csv/…)")


def _split_recursive(text: str, sep_idx: int = 0, budget: int | None = None) -> list[str]:
    """Recursively split on a separator hierarchy, keeping semantic units intact and
    never straddling a section boundary. Falls through coarser→finer separators until
    pieces fit `budget` chars. Re-appends the (stripped) separator at flush boundaries so
    sentence punctuation isn't lost from citation quotes."""
    budget = budget or _CHUNK_CHARS
    seps = ["\n\n", "\n", ". ", " ", ""]
    if len(text) <= budget:
        return [text] if text.strip() else []
    if sep_idx >= len(seps):
        return [text[i:i + budget] for i in range(0, len(text), budget)]
    sep = seps[sep_idx]
    parts = text.split(sep) if sep else list(text)
    out, buf = [], ""
    for part in parts:
        piece = (buf + sep + part) if buf else part
        if len(piece) <= budget:
            buf = piece
        else:
            if buf.strip():
                out.append((buf + sep.rstrip()) if sep.strip() else buf)
            buf = part if len(part) <= budget else ""
            if len(part) > budget:
                out.extend(_split_recursive(part, sep_idx + 1, budget))
    if buf.strip():
        out.append(buf)
    return out


def _chunk(text: str) -> list[str]:
    """Structure-aware chunking (R8/D20): split on markdown/section headers first, then
    recurse paragraph→line→sentence→word so a chunk never straddles a section. Light
    overlap as boundary insurance (parent-window expansion supplies the rest of context)."""
    if not text or not text.strip():
        return []
    # 1) split on headers (markdown #, ##… and underline/blank-separated sections)
    sections, cur = [], []
    for line in text.splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s", line) and cur:
            sections.append("\n".join(cur)); cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append("\n".join(cur))
    # 2) recurse within each section to a size that leaves room for the overlap tail,
    #    so a stored chunk (raw + prepended overlap) stays within _CHUNK_CHARS.
    budget = max(400, _CHUNK_CHARS - _OVERLAP)
    raw = []
    for sec in sections:
        raw.extend(_split_recursive(sec.strip(), budget=budget))
    raw = [c.strip() for c in raw if c.strip()]
    # 3) light char overlap between adjacent chunks (boundary insurance)
    if _OVERLAP <= 0 or len(raw) < 2:
        return raw
    out = [raw[0]]
    for prev, c in zip(raw, raw[1:]):
        tail = prev[-_OVERLAP:]
        out.append((tail + " " + c) if tail else c)
    return out


# ------------------------------------------------------------------ embeddings
def embed(texts: list[str]) -> list[list[float]]:
    """Embed via local Ollama. One call per text (Ollama's embeddings API is single-input)."""
    vecs = []
    for t in texts:
        r = requests.post(f"{OLLAMA}/api/embeddings",
                          json={"model": EMBED_MODEL, "prompt": t}, timeout=120)
        r.raise_for_status()
        vecs.append(r.json()["embedding"])
    return vecs


# --------------------------------------------- contextual retrieval (Anthropic technique)
def _smallest_chat_model() -> str:
    """Pick the smallest installed non-embedding model for cheap contextual blurbs."""
    if _CTX_MODEL:
        return _CTX_MODEL
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=10)
        models = [m for m in r.json().get("models", []) if "embed" not in m["name"].lower()]
        return sorted(models, key=lambda m: m.get("size", 0))[0]["name"]
    except Exception:
        return ""


def _situate(doc_text: str, chunk: str, model: str) -> str:
    """Anthropic Contextual Retrieval: a 1-2 sentence blurb situating the chunk in its
    document, to be prepended before embedding + BM25 indexing. Best-effort ('' on failure)."""
    if not model:
        return ""
    try:
        from council.sanitize import spotlight
        # the document is untrusted content → spotlight it so a planted instruction inside
        # the file can't hijack the blurb-writer (data, not instructions)
        body = spotlight(f"<document>\n{doc_text[:8000]}\n</document>\n<chunk>\n{chunk[:1500]}\n</chunk>")
        prompt = (f"{body}\n"
                  "Give a short succinct context (1-2 sentences) to situate this chunk within "
                  "the overall document for improving search retrieval. Answer ONLY with the "
                  "context, nothing else.")
        r = requests.post(f"{OLLAMA}/api/generate",
                          json={"model": model, "prompt": prompt, "stream": False,
                                "options": {"temperature": 0.0, "num_predict": 120}},
                          timeout=120)
        r.raise_for_status()
        return (r.json().get("response") or "").strip()[:400]
    except Exception:
        return ""


# ------------------------------------------------------------------ store
class Library:
    def __init__(self, db_path: pathlib.Path = LIB_DB):
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._corpus_key = None
        self._corpus_cache = None
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks("
            "id INTEGER PRIMARY KEY, source TEXT, title TEXT, ord INT, text TEXT, vec TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS filemeta(source TEXT PRIMARY KEY, fhash TEXT)")
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(chunks)")}
        if "context" not in cols:   # contextual-retrieval blurb (R8); display still uses `text`
            self.conn.execute("ALTER TABLE chunks ADD COLUMN context TEXT")
        self.conn.commit()

    def add(self, path: str) -> int:
        """Index a file or directory (confined to PW_LIBRARY_ROOTS). Returns chunks added."""
        p = pathlib.Path(path).expanduser().resolve()
        if not _within_roots(p):
            raise ValueError(f"path outside allowed roots ({', '.join(map(str, _ROOTS))}); "
                             "set PW_LIBRARY_ROOTS to widen")
        if p.is_dir():
            total = files = tbytes = 0
            for f in sorted(p.rglob("*")):
                if files >= _MAX_FILES or tbytes >= _MAX_TOTAL_BYTES:
                    print(f"  stop: ingest cap reached ({_MAX_FILES} files / "
                          f"{_MAX_TOTAL_BYTES//1_000_000} MB)", flush=True)
                    break
                if f.is_symlink():            # don't follow symlinks out of the tree
                    continue
                if f.is_file() and f.suffix.lower() in _TEXT_EXT | {".pdf", ".docx"} \
                        and _within_roots(f):
                    try:
                        sz = f.stat().st_size
                        if sz > _MAX_FILE_BYTES:
                            print(f"  skip {f.name}: too large ({sz//1_000_000} MB)", flush=True)
                            continue
                        added = self._add_file(f)
                        if added:
                            files += 1
                            tbytes += sz
                        total += added
                    except Exception as e:
                        print(f"  skip {f.name}: {e}", flush=True)
            return total
        return self._add_file(p)

    def _add_file(self, p: pathlib.Path) -> int:
        if not _within_roots(p):
            raise ValueError("path outside allowed roots")
        if p.is_file() and p.stat().st_size > _MAX_FILE_BYTES:
            raise ValueError(f"file too large (> {_MAX_FILE_BYTES//1_000_000} MB)")
        src = str(p)
        fhash = hashlib.sha256(p.read_bytes()).hexdigest()
        prev = self.conn.execute("SELECT fhash FROM filemeta WHERE source=?", (src,)).fetchone()
        if prev and prev["fhash"] == fhash and not self.is_empty():
            print(f"  unchanged {p.name} — skipped (incremental)", flush=True)
            return 0
        doc_text = extract_text(p)
        chunks = _chunk(doc_text)
        if not chunks:
            return 0
        title = p.name
        # Contextual Retrieval: always prepend the title (free baseline); optionally an
        # LLM situating blurb (flag-gated for speed). Embed/BM25 use context+text; the
        # displayed [L#] quote uses `text` only — the blurb never leaks into citations.
        ctx_model = _smallest_chat_model() if _CONTEXTUAL else ""
        contexts, augmented = [], []
        for c in chunks:
            blurb = _situate(doc_text, c, ctx_model) if ctx_model else ""
            ctx = (f"{title}. {blurb}".strip()) if blurb else title
            contexts.append(ctx)
            augmented.append(f"{ctx}\n{c}")
        self.conn.execute("DELETE FROM chunks WHERE source=?", (src,))   # re-index = replace
        vecs = embed(augmented)
        self.conn.executemany(
            "INSERT INTO chunks(source,title,ord,text,context,vec) VALUES(?,?,?,?,?,?)",
            [(src, title, i, c, ctx, json.dumps(v))
             for i, (c, ctx, v) in enumerate(zip(chunks, contexts, vecs))])
        self.conn.execute("INSERT OR REPLACE INTO filemeta(source,fhash) VALUES(?,?)", (src, fhash))
        self.conn.commit()
        tag = " (contextual)" if ctx_model else ""
        print(f"  indexed {title}: {len(chunks)} chunks{tag}", flush=True)
        return len(chunks)

    def remove(self, path: str) -> int:
        src = str(pathlib.Path(path).expanduser().resolve())
        cur = self.conn.execute("DELETE FROM chunks WHERE source=?", (src,))
        self.conn.execute("DELETE FROM filemeta WHERE source=?", (src,))
        self.conn.commit()
        return cur.rowcount

    def clear(self) -> None:
        self.conn.execute("DELETE FROM chunks")
        self.conn.execute("DELETE FROM filemeta")
        self.conn.commit()

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT source, title, COUNT(*) n FROM chunks GROUP BY source ORDER BY title")]

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is None

    def _corpus(self):
        """Load rows + build the normalized embedding matrix and BM25 index ONCE, cached on a
        cheap fingerprint (chunk count + max id) so repeated searches (e.g. 3 analysts/run, or
        the serve/MCP loop) don't rebuild the whole index each call."""
        import numpy as np
        from council.retrieval import BM25Okapi, tokenize
        fp = self.conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM chunks").fetchone()
        key = (fp[0], fp[1])
        if getattr(self, "_corpus_key", None) == key and self._corpus_cache is not None:
            return self._corpus_cache
        rows = list(self.conn.execute("SELECT source,title,ord,text,context,vec FROM chunks"))
        if rows and any(r["context"] is None for r in rows) and any(r["context"] for r in rows):
            print("  note: library mixes pre/post-contextual rows — `pw library clear` then "
                  "re-add for a consistent index", flush=True)
        mat = np.asarray([json.loads(r["vec"]) for r in rows], dtype="float32")
        norm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        bm25 = BM25Okapi([tokenize((r["context"] or "") + " " + r["text"]) for r in rows])
        self._corpus_key, self._corpus_cache = key, (rows, norm, bm25)
        return self._corpus_cache

    def search(self, query: str, k: int = 5, window: bool = True) -> list[dict]:
        """Hybrid retrieval (R8/D20): dense cosine ⊕ BM25 lexical, fused by Reciprocal Rank
        Fusion, then small-to-big parent-window expansion. Catches both semantic matches and
        exact terms (names/codes/numbers) dense embeddings miss. [] on empty/failure."""
        if self.is_empty():
            return []
        try:
            import numpy as np
            from council.retrieval import reciprocal_rank_fusion
            rows, norm, bm25 = self._corpus()
            pool = min(50, len(rows))   # candidates per retriever before fusion
            qv = np.asarray(embed([query])[0], dtype="float32")
            qn = qv / (np.linalg.norm(qv) + 1e-9)
            sims = norm @ qn
            dense_rank = list(np.argsort(sims)[::-1][:pool])
            sparse_rank = bm25.top(query, k=pool)
            fused = reciprocal_rank_fusion([list(map(int, dense_rank)), list(map(int, sparse_rank))],
                                           top_k=max(k, 15) if _RERANK else k)
            if _RERANK and len(fused) > k:
                fused = self._rerank(query, rows, fused, k)
            return [self._hit(rows, i, float(sims[i]), window) for i in fused[:k]]
        except Exception:
            return []

    def _rerank(self, query: str, rows, ids: list[int], k: int) -> list[int]:
        """Opt-in (PW_RERANK=1) listwise rerank: one local-model call scores the fused
        candidates by actual relevance. Zero new deps; falls back to RRF order on failure."""
        model = _smallest_chat_model()
        if not model:
            return ids[:k]
        try:
            from council.judge import _extract_json
            from council.sanitize import spotlight
            # candidate passages are untrusted document text → spotlight (a planted
            # instruction can at worst reorder, never escape; index-clamp below bounds it)
            cand = spotlight("\n".join(f"[{j}] {rows[i]['text'][:300]}" for j, i in enumerate(ids)))
            prompt = (f"Rank the passages by relevance to the QUERY. Return STRICT JSON: "
                      f'{{"order":[indices best-first]}}.\n\nQUERY: {query}\n\nPASSAGES:\n{cand}\n\nJSON:')
            r = requests.post(f"{OLLAMA}/api/generate",
                              json={"model": model, "prompt": prompt, "stream": False,
                                    "options": {"temperature": 0.0, "num_predict": 120}}, timeout=120)
            r.raise_for_status()
            parsed = _extract_json((r.json().get("response") or "").strip())
            order = parsed.get("order") if isinstance(parsed, dict) else None
            if isinstance(order, list):
                picked = [ids[j] for j in order if isinstance(j, int) and 0 <= j < len(ids)]
                # append any not mentioned, preserving fusion order
                picked += [i for i in ids if i not in picked]
                return picked[:k]
        except Exception:
            pass
        return ids[:k]

    def _hit(self, rows, i, score, window: bool) -> dict:
        """Build a result, optionally expanding to a parent window (neighbor chunks of the
        same source) for richer grounding. Display text never includes the context blurb."""
        r = rows[i]
        text = r["text"]
        if window:
            same = {row["ord"]: row["text"] for row in rows if row["source"] == r["source"]}
            parts = [same[o] for o in (r["ord"] - 1, r["ord"], r["ord"] + 1) if o in same]
            text = " […] ".join(parts)[:2500]
        return {"source": r["source"], "title": r["title"], "ord": r["ord"],
                "text": text, "score": score}


# ------------------------------------------------------------------ CLI
def main() -> int:
    args = sys.argv[1:]
    lib = Library()
    if not args or args[0] == "list":
        srcs = lib.sources()
        if not srcs:
            print("library empty — add files with: pw library add <path>")
        else:
            for s in srcs:
                print(f"  {s['title']:40s} {s['n']:4d} chunks   {s['source']}")
            print(f"\n{len(srcs)} document(s) indexed.")
        return 0
    cmd = args[0]
    if cmd == "add":
        if len(args) < 2:
            print("usage: pw library add <path|dir>"); return 2
        n = lib.add(args[1])
        print(f"✓ {n} chunks indexed.")
        return 0
    if cmd == "remove":
        if len(args) < 2:
            print("usage: pw library remove <path>"); return 2
        print(f"✓ removed {lib.remove(args[1])} chunks.")
        return 0
    if cmd == "clear":
        lib.clear(); print("✓ library cleared.")
        return 0
    print(f"unknown: pw library {cmd}  (add | list | remove | clear)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
