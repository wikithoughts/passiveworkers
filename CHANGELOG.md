# Changelog

All notable changes to Passive Workers are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/). See `docs/ROADMAP.md` for the full
round-by-round (R#) history and `docs/DECISIONS.md` for the rationale behind each change.

## [0.4.0] — 2026-07-10
A broad round (R36 / D52): two flagship research-quality levers, a code-hardening sweep, and a
bigger-model evidence pass. Everything is pure local code; adversarially reviewed before commit.
### Added
- **Web-evidence reranking.** On non-time-sensitive briefs, `pw research` now reranks the web search
  results by relevance to the brief *before* the context cap and page-fetch, so the strongest sources
  survive and get read in full — instead of drafting from whatever order the search backend returned.
  Reuses the same one-call local reranker as the private library (`council/rerank.py`, shared).
  Default on; `PW_RESEARCH_RERANK=0` opts out. (Time-sensitive briefs keep recency ordering, unchanged.)
- **Adaptive (recursive) research depth.** The fixed refine rounds became a *budgeted* loop: it keeps
  issuing gap-filling follow-up queries while the model still finds gaps and each round surfaces new
  sources, then stops — bounded by round, wall-clock (`PW_RESEARCH_DEADLINE`, default 240s), and source
  (`PW_RESEARCH_MAX_SOURCES`, default 30) budgets. Hard briefs go deeper; well-covered ones stop early.
- Measured (gemma3:12b/4b, `--depth standard`): the rerank held the grounded rate (88% both arms) and
  nudged mean source-overlap 68%→71% — directional, since between-runs variance dominates the magnitude
  on a small local rig; and with **all** levers on the free currency gap held (static ≈tie +0.25, recent
  +3.0), i.e. **no regression**. The levers are safe by construction (rerank only reorders; deeper search
  only adds evidence). Full numbers in the `docs/BENEFIT.md` R36 appendix.
### Fixed
- **Escrow refund no longer strands an asker's hold.** When an assisted offer expired, a refund that
  raised (e.g. a ledger desync) still marked the job failed — losing the held credit. The reaper now
  leaves the offer open to retry on the next tick, and `Ledger.refund` is atomic (it resolves the asker
  account before moving any credit, so a missing account can't decrement escrow and burn credit).
### Internal
- **One dark theme for all three web UIs.** `council/net/ui_common.py` now holds the canonical `:root`
  palette, footer, Leaflet/CARTO map bootstrap, and status colors — ending the drift between the research
  desk, marketplace, and operator dashboard (three different `--bg`/`--card` values, a `--muted`/`--mut`
  name split, and a divergent map attribution). All three surfaces browser-verified to render coherently.
- **Scoped type-checking.** A `[tool.pyright]` config (`basic`, `council/net` only) + a non-blocking
  advisory CI `types` job. Fixed the genuine annotation issues it surfaced (`Store.__init__` path,
  `create_job` `then` accepting a pipeline list, a heterogeneous profile dict).
- Tests for the previously-uncovered web-UI routes (`GET /`, `/dashboard`) and the legacy in-process
  `Council` orchestrator; the shared reranker and the adaptive-depth budgets are unit-pinned.

## [0.3.0] — 2026-07-10
A "quality + proof" round (R35 / D51): the flagship reports now self-check their own citations at
inference time, and the benefit evidence gets stronger **without any paid API spend**. Both halves are
pure local code; every change was adversarially reviewed before commit.
### Added
- **Citation-fidelity self-repair in `pw research`.** After each analyst drafts, the engine scores every
  cited claim against the exact source text the model saw (the same `council.fidelity` grounding check
  that powers the offline eval) and, when a claim is unsupported or asserts a number/date absent from its
  source, does one bounded re-prompt to correct or drop just that claim. The revision is **accepted only
  if it measurably reduces the unsupported set without losing grounded content** — so by its own metric
  the pass can never lower a report's grounding, and it adds a model call only when there is something to
  fix. On by default; `PW_FIDELITY_REPAIR=0` opts out. Measured paired (same-run, same-evidence via
  `--paired`) on local Ollama: it removes fixable unsupported claims with **zero regression by
  construction**; on a capable analyst (gemma3:12b) an illustrative run improved the grounded rate
  85%→88% (2 of 4 drafts repaired), while the weaker gemma3:4b analyst safely *declined* every revision
  (no change). A small, honest effect on a 2-model rig — the guarantee, not the magnitude, is the point.
- **Free local baseline for the currency eval.** `eval_currency_gap.py --baseline local` compares the
  council (local models + live web) against the **same local models answering from their own memory** —
  no web, no API key, **$0**. This is the apples-to-apples read the product's claim actually makes (and
  removes the paid frontier dependency that gated deepening this eval). The paid frontier stays available
  as `--baseline frontier` (default, unchanged). The question bank was expanded and re-verified from the
  live web as of 2026-07-10 (4 static / 7 recent / 5 breaking) so the two moving windows clear the
  "paired n < 3 = noise" floor. Measured (gemma3:4b + live web vs the same model from memory, graded
  0–10 vs curated refs, `$0`): static control ≈ tie (**−0.5**, baseline even slightly ahead — the eval
  isn't rigged), **recent +4.6 (n=7)**, **breaking +4.0 (n=5)**, overall +3.1.
- `eval_citation_fidelity.py --paired` (confound-free: scores the same draft pre/post-repair against the
  same evidence) and `--compare` (between-runs) — measure the self-repair's grounded-rate effect.
### Changed
- The currency-eval result/matrix keys are `baseline_*` (was `frontier_*`), and the matrix now labels
  which baseline it ran against; the API-key requirement and the `MAX_PAID_QUESTIONS` ceiling apply only
  to runs that actually spend (frontier baseline and/or api grader).
### Internal
- `council/fidelity.py` gained pure `unsupported_claims` / `grounded_counts` / `grounded_word_count`
  helpers (no Ollama/network); `ResearchWorker`'s source-block rendering was factored into `_ev` /
  `_source_block` and reused by the repair pass. New `tests/test_critic.py` pins the acceptance gate.

## [0.2.0] — 2026-07-05 – 07-07
A broad "usable, trustworthy, competitive" pass (R32 / D48): security/privacy hardening, engine
reliability, new CLI + export features, marketplace UX, and a hardened trust surface — plus a
configuration & connectivity round (R33 / D49): persistent `pw config` and keyed search backends —
and an engineering-debt round (R34 / D50): pricing consolidation, a shared UI module, and test
coverage for the operator/asker CLI (which caught a broken `pw rate`). Every phase was adversarially
reviewed before commit — the reviews caught real defects the green test suite had missed.
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
- **`pw config`** — persist any setting (Ollama URL, models, web backend, API keys) once to an
  owner-only (0600) `~/.passiveworkers/config.json` instead of re-`export`-ing env vars each shell;
  `get`/`set`/`unset`/`list` with secrets masked. Precedence: explicit env var > config file > default.
- **Keyed web search (Brave / Tavily / Serper)** — opt-in alternatives to DuckDuckGo, which
  rate-limits at scale. A configured keyed backend is also used **automatically as a fallback** when
  DDG fails, so research stays reliable. Honest tradeoff: keyed engines are central APIs and do not
  geo-localize on your egress like DDG/SearXNG (documented; default stays DDG).
- **`pw status` / `pw doctor`** — a one-second preflight (Ollama up? which models? library? joined?
  which search backend, and is its key set?).
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
- **`pw rate <job> <score>` never worked:** it posted its JSON body without a `Content-Type`, so the
  coordinator rejected it (422). It now sends `application/json` (found by the new client test coverage).
- All-errored council **or batch** jobs are marked failed and the asker is not charged.
- Bounded desk concurrency + job-map pruning (+ 404-safe polling); nested `--out` dirs; non-Latin report
  filenames; Ctrl-C handling; balanced parens in exported citation links.
### Internal (R34 / D50)
- The worker-pool price is derived in one place (`council.net.config.pool_for`) instead of copy-pasted
  across 6 sites; the marketplace cost preview now reads real prices from `/job-types` (its hardcoded
  `30/20/10` fallback lied the moment an operator retuned `PW_WORKER_POOL`/`PW_FLEET_SIZE`/`PW_JUDGE_FEE`).
- A shared `council/net/ui_common.py` (HTML-escape helper + country-centroid table) removes drift across
  the three web UIs — an unmapped/`local` node now resolves to the same spot on both maps.
- The operator/asker CLI client (`pw tasks`/`accept`/`deliver`/`rate` and `council.net.submit`) gained
  integration test coverage via a `requests`→`TestClient` shim (previously untested).

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

[0.4.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.4.0
[0.3.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.3.0
[0.2.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.2.0
[0.1.5]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.5
[0.1.4]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.4
[0.1.3]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.3
[0.1.2]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.2
[0.1.1]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.1
[0.1.0]: https://github.com/wikithoughts/passiveworkers/releases/tag/v0.1.0
