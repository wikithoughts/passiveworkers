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
import sqlite3
import sys

import requests

OLLAMA = os.environ.get("PW_OLLAMA_BASE", "http://localhost:11434")
EMBED_MODEL = os.environ.get("PW_EMBED_MODEL", "nomic-embed-text")
LIB_DIR = pathlib.Path(os.environ.get("PW_LIBRARY_DIR",
                                      str(pathlib.Path.home() / ".passiveworkers")))
LIB_DB = LIB_DIR / "library.db"
_CHUNK_CHARS = 1400          # ~350 tokens — fits small-model context comfortably
_OVERLAP = 200
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


def _chunk(text: str) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + _CHUNK_CHARS])
        i += _CHUNK_CHARS - _OVERLAP
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


# ------------------------------------------------------------------ store
class Library:
    def __init__(self, db_path: pathlib.Path = LIB_DB):
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks("
            "id INTEGER PRIMARY KEY, source TEXT, title TEXT, ord INT, text TEXT, vec TEXT)")
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
        chunks = _chunk(extract_text(p))
        if not chunks:
            return 0
        self.conn.execute("DELETE FROM chunks WHERE source=?", (src,))   # re-index = replace
        vecs = embed(chunks)
        title = p.name
        self.conn.executemany(
            "INSERT INTO chunks(source,title,ord,text,vec) VALUES(?,?,?,?,?)",
            [(src, title, i, c, json.dumps(v)) for i, (c, v) in enumerate(zip(chunks, vecs))])
        self.conn.commit()
        print(f"  indexed {title}: {len(chunks)} chunks", flush=True)
        return len(chunks)

    def remove(self, path: str) -> int:
        src = str(pathlib.Path(path).expanduser().resolve())
        cur = self.conn.execute("DELETE FROM chunks WHERE source=?", (src,))
        self.conn.commit()
        return cur.rowcount

    def clear(self) -> None:
        self.conn.execute("DELETE FROM chunks")
        self.conn.commit()

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT source, title, COUNT(*) n FROM chunks GROUP BY source ORDER BY title")]

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is None

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Top-k chunks by cosine similarity. [] if the library is empty or embedding fails."""
        if self.is_empty():
            return []
        try:
            import numpy as np
            qv = np.asarray(embed([query])[0], dtype="float32")
            rows = list(self.conn.execute("SELECT source,title,ord,text,vec FROM chunks"))
            mat = np.asarray([json.loads(r["vec"]) for r in rows], dtype="float32")
            qn = qv / (np.linalg.norm(qv) + 1e-9)
            mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
            sims = mn @ qn
            top = sims.argsort()[::-1][:k]
            return [{"source": rows[i]["source"], "title": rows[i]["title"],
                     "ord": rows[i]["ord"], "text": rows[i]["text"],
                     "score": float(sims[i])} for i in top]
        except Exception:
            return []


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
