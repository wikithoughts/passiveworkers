# Changelog

All notable changes to Passive Workers are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/). See `docs/ROADMAP.md` for the full
round-by-round (R#) history and `docs/DECISIONS.md` for the rationale behind each change.

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

[0.1.4]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.4
[0.1.3]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.3
[0.1.2]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.2
[0.1.1]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.1
[0.1.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.0
