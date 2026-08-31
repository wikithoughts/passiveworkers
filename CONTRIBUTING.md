# Contributing

Passive Workers is young software with an honest culture: we publish our methodology and
our losses (see `docs/TRIAL_RESULTS.md`), and we'd rather a small thing that runs than a
big thing that doesn't.

## Quick start for contributors

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,all]'   # dev tools (pytest/ruff) + every production extra CI tests against
ollama pull qwen3:4b          # any small model works for development
pworkers research "test brief" --quick --analysts 1
```

Also needs Node 18+ on `PATH` for gates 4–5 below (`brew install node` / `apt install nodejs`) —
Node isn't pip-installable, so it's a separate one-time step.

## Before opening a PR

1. `python -m py_compile $(git ls-files '*.py')` — everything compiles.
2. `ruff check .` — lint gate (matches CI).
3. `pytest tests/ -q` — the full test suite is green (~5s, 400+ tests, fully offline — CI's
   headline step).
4. `bash scripts/check_app_js.sh` — inline JS in served pages parses.
5. `node scripts/fe_test.js` — the front-end harness passes.
6. If you touched anything that handles web content: the sanitizer rules in
   `passiveworkers/sanitize.py` are load-bearing (see the Security model in the README).
   Don't weaken them; add tests if you extend them.

## Ground rules (from docs/DECISIONS.md — the short version)

- No browser automation / computer-use. Search APIs + plain fetch of public pages only.
- Nodes return their own findings, never proxied traffic (D4).
- No tokens, no tradeable credit, ever (D1).
- Honest comparisons: if you add a benchmark, length-control and position-swap it.

Questions → open an issue. Thanks for helping build the commons.
