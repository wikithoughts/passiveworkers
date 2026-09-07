# Architecture and terminology

Read this before changing package layout, dependency imports, installation support or terminology. Backticked paths are repository-root relative.

## Stack & layout

- **Language:** Python 3.10–3.14 (see `pyproject.toml` classifiers), package
  `passiveworkers`, CLI entry point `pworkers` (`passiveworkers.cli:main`). A small
  amount of inline JS is served directly from Python string constants — there is no
  separate JS build toolchain or bundler, just a syntax guard (see Commands).
- **Package manager:** pip / `pyproject.toml` is the source of truth for dependencies.
  `requirements.txt` is a documented convenience mirror only — its own header comment
  says `pyproject.toml` wins on any disagreement.
- **Frameworks:** FastAPI + uvicorn (the coordinator/dashboard/serve HTTP surfaces),
  pydantic, pytest + pytest-cov (test), ruff (lint). Optional extras (`trafilatura`,
  `pypdf`/`python-docx`, `mcp`, `pynacl`, `geoip2`) are all lazy-imported — CI's
  `core-install` job specifically checks the package installs and runs with **zero**
  extras present, so a new hard import of an optional dep is a regression.
- **Top-level layout:**
  - `passiveworkers/` — the package: `cli.py`, `local.py`, `serve.py`, `render.py`,
    `sanitize.py` (prompt-injection defense, see Guardrails), `paths.py`/`config.py`
    (0600 credential storage), `coordinator.py`, `operator.py`, `doctor.py`,
    `mcp_server.py`, `ledger.py`, `crypto.py`, and `net/` (the FastAPI apps:
    `app.py`, `dashboard.py`, `coordinator_app.py`, `agent.py`, the `_store_*`
    modules).
  - `tests/` — the pytest suite (57 files, offline).
  - `scripts/` — ops/eval/bench/deploy scripts (`check_app_js.sh`, `fe_test.js`,
    `deploy_vps.sh`, `install_systemd.sh`, `mac_join.sh`, benchmark/eval scripts) —
    not pip-installable, run directly.
  - `docs/` — the real source of truth for positioning and decisions (`VISION.md`,
    `DECISIONS.md`, `ROADMAP.md`, `GLOSSARY.md`, `network/`, …) — see Where to find
    more.
  - `SECURITY.md`, `CONTRIBUTING.md`, `llms.txt` at the root.

## Vocabulary note

Round-32-onward code and docs use: analyst / blind judge / editor / research desk
(single-player), and coordinator / operator / asker / assisted task / credit
(network). Some older files still say "The Council," "Substrate," "Perspective," or
"Worker" — these are the same concepts under earlier names, not different systems. See
[docs/GLOSSARY.md](../GLOSSARY.md) for the full current-vs-deprecated mapping before
assuming two terms mean two different things.


