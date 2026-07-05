# Changelog

All notable changes to Passive Workers are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/). See `docs/ROADMAP.md` for the full
round-by-round (R#) history and `docs/DECISIONS.md` for the rationale behind each change.

## [0.2.0] — 2026-07-05
A broad "usable, trustworthy, competitive" pass (R32 / D48): security/privacy hardening, engine
reliability, new CLI + export features, marketplace UX, and a hardened trust surface. Every phase was
adversarially reviewed before commit — which caught 15 real defects the green test suite had missed.
### Security
- **`/status` privacy:** the public dashboard feed no longer exposes the per-account balance sheet or
  asker handles / job-ids — only a de-identified pulse (type · status · age). Pseudonymous operator
  ranking stays at `/leaderboard`; a user reads their own balance at `/me`.
- **`GET /jobs/{id}` is now a capability URL:** the result stays readable by the unguessable id (the
  shareable link), but the asker's identity, credit receipt, settlement error, and pipeline chain-ids
  are returned only to the authenticated asker.
- Constant-time admin-token compare (was a timing side channel; a non-ASCII token now 401s, not 500s).
- Enrollment/signup tokens redeem atomically with the node/user they gate — a failed insert or a
  "handle taken" collision no longer burns a single-use token or leaves a phantom ledger account.
### Added
- **`pw status` / `pw doctor`** — a one-second preflight (Ollama up? which models? library? joined?).
- **`pw version`**, **`pw reports`**, **`pw library search <query>`**.
- **`pw research --json`** (report + deduped web+library sources + per-analyst stats) and **`--html`**
  (a self-contained, printable page) — plus a "Save as PDF" button on the research desk.
- Research desk: a sources selector (web / library / both), a **Cancel** button, and `PW_SERVE_PORT`.
- Marketplace: **account recovery** ("I have a key") + show-key-once on signup.
### Changed
- **Remote Ollama actually works:** one shared `council/ollama.py` so `PW_OLLAMA_BASE` reaches the
  analysts, editor, worker, and batch (they had hardcoded localhost) — four duplicate clients become one.
- Reports default to a shared `~/.passiveworkers/reports` so `pw research`, `pw serve`, and `pw mcp`
  share one history regardless of directory (`PW_REPORTS_DIR` / `--out` override).
- `--editor api` validates the key BEFORE the multi-minute run; the marketplace cost preview reads real
  prices from `GET /job-types`; the "contribute your computer" snippet uses `pw join`.
- CI gains a `ruff` lint gate, a Python 3.10–3.13 matrix, coverage, and a core-only-install job; the
  README gains badges, an architecture diagram, and a comparison table.
### Fixed
- All-errored council **or batch** jobs are marked failed and the asker is not charged.
- Bounded desk concurrency + job-map pruning (+ 404-safe polling); nested `--out` dirs; non-Latin report
  filenames; Ctrl-C handling; balanced parens in exported citation links.

## [0.1.5] — 2026-06-14
`pw join` actually works now — two bugs found by dogfooding the real operator flow on a VPS (R31 / D47).
### Fixed
- **Enrollment-mode auth:** an operator who joined with `pw join` (and so never has the shared admin
  token) was rejected (401) on every authenticated call after registering — heartbeat, task poll,
  result, progress, assisted offers/accept/deliver, blob upload — so every job it touched failed. The
  per-node secret now authenticates those endpoints on its own (it's minted at register, itself gated).
  Backward-compatible. This made `pw join` + enrollment usable end-to-end for the first time.
- **Lone-operator judging:** `pw join` defaulted to non-judging, so a single-operator deployment failed
  every job with "no judge node online". Judging is now **on by default** (reuses the answer model);
  `--no-judge` opts out. A lone operator can now answer *and* judge a job end-to-end.

## [0.1.4] — 2026-06-14
Network features + a UI/UX polish pass (R29–R30 / D42–D46).
### Added
- **`pw join <url> <token>` / `pw work`** — one-command operator onboarding: persists identity to
  `~/.passiveworkers/join.json` (owner-only), redeems via the existing enrollment path, seeds the
  agent's env, and resumes from cache. Backward-compatible with the env-var flow.
- **Offline GeoIP verification** (`[geoip]` extra, `PW_GEOIP_DB`): verify a node's self-reported
  country against the IP the coordinator saw — no egress, spoof-resistant (`PW_TRUST_XFF`-gated),
  surfaced as `geo_country`/`geo_mismatch` (never the IP). Default-off → falls back to self-reported.
- **Operator leaderboard** (`GET /leaderboard` + dashboard card): pseudonymous, ranked by
  reputation / jobs helped / credits earned.
### Changed
- UI/UX polish across the research desk, marketplace app, and operator dashboard: accessibility
  (labels, live-regions, focus), mobile responsiveness, button states + a loading spinner, a unified
  wordmark, and a version footer (injected, not hardcoded).
- Packaging metadata + a CHANGELOG and git tags landed in 0.1.3; this continues that hygiene.
### Fixed
- `pw join` config write is owner-only even on the fallback path (no world-readable window for the
  node secret).

## [0.1.3] — 2026-06-14
First-run robustness and a benefit-to-people showcase (R28 / D40–D41).
### Added
- `docs/USE_CASES.md` — 15 concrete, runnable, honesty-bounded scenarios (privacy & confidentiality,
  access & cost, sovereignty & honest citations, the commons), each with the exact `pw` command, plus a
  README "Who this is for" section.
- Friendlier first-run errors: Ollama-not-running prints `Start it with ` + "`ollama serve`" instead of a
  traceback; a missing optional extra hints `pip install 'passiveworkers[docs]'` / `[mcp]`.
- Flagship-path tests: `tests/test_local.py`, `tests/test_mcp.py`, `tests/test_serve.py`.
### Fixed
- `SystemExit` (a `BaseException`) no longer slips past `except Exception` in the library directory walk
  (a missing `[docs]` extra now halts with the install hint instead of silently skipping every PDF), in the
  MCP `library_add` boundary, or in the `pw serve` worker (a job no longer hangs forever on Ollama-down).
### Packaging
- PEP 639 SPDX `license = "MIT"`, expanded classifiers, authors, and project URLs (Issues/Docs/Changelog).

## [0.1.2] — 2026-06-14
Phase-3 tail of the distributed network (R26–R27 / D38–D39).
### Added
- Multi-producer **file reassembly**: a sharded job (`as_file=True`) returns its combined output as one
  downloadable, content-addressed, integrity-verified file instead of a JSON array.
- Generic **multi-stage pipeline**: `then` accepts a list of stages of any task type, self-propagating
  (e.g. `code_generation → assisted → assisted`), backward-compatible with the single-hop `then`.

## [0.1.1] — 2026-06-14
The distributed task network (R20–R25 / D32–D37).
### Added
- Distributed task orchestration: capacity-weighted split, progress reporting, and failover.
- Task-type registry with `download_extract` and `code_generation` types; stage chaining.
- Public-launch auth: per-identity rate limiting and per-operator enrollment tokens (Sybil closure).
### Security
- Hardened the asker-influenced fetch + upload surface (SSRF redirect checks, blob memory caps).

## [0.1.0] — 2026-06-13
Initial public release — the single-player deep-research engine.
### Added
- `pw research` — multiple local models research the live web as independent analysts; a blind editor
  compiles one cited markdown report. `pw serve` — a local research desk UI.
- Private-document RAG (`pw library add` + `--local`), MCP server (`pw mcp`), and the opt-in network layer
  (`council/net/`) with a live two-country deployment.
- Eval instruments: SimpleQA bench, citation-fidelity, and currency-gap scripts.

[0.2.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.2.0
[0.1.5]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.5
[0.1.4]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.4
[0.1.3]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.3
[0.1.2]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.2
[0.1.1]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.1
[0.1.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.0
