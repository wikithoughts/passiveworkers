# Passive Workers — Roadmap

> The living tracker: where we are, what's next, and the bar each step must clear.
> Status legend: ✅ done · ⏭ in progress / next · ◻︎ planned.

## M0 — Phase-0 spikes ✅
De-risk the two pieces most likely to break.
- ✅ **Spike 2** (worker/Ollama wrapper) — hardware profiling, job round-trip, CPU-ceiling pause gate. `spikes/spike2_worker/worker.py`.
- ◻︎ **Spike 1** (verification comparator) — cheat-rejection validated on **one** machine (honest 0.9997 vs cheat 0.4366, threshold 0.82). **Still unverified:** honest *cross-hardware* divergence and *model-downgrade* detection (needs ≥3 heterogeneous machines — now unblocked by M2). `spikes/spike1_fuzzy_verify/verify.py`.

## M1 — Council MVP (local, in-process) ✅
The whole idea at single-machine, multi-model scale.
- ✅ Diverse models → **blind** judge scoring (ideas compete) → diversity-preserving **merge** → non-transferable ledger with **give/take enforcement** and **exact conservation** (rounding bug fixed; 3,000 randomized trials, zero drift).
- ✅ Result: merge beat best-single **2/2** (honest caveat: LLM judge has length bias → see M3). Files: `council/{ledger,worker,judge,coordinator,run_demo}.py`. Run: `python -m council.run_demo`.

## M2 — Networked two-machine Council (Mac ↔ VPS) ✅ (core)
Made the geo-diversity moat real. Coordinator on the Hetzner VPS **wikiclaw-1** (Helsinki, FI),
isolated in `/opt/passiveworkers`, **reusing the host's existing Ollama**, bound to `127.0.0.1`
(zero new public ports); the Mac reaches it over an **SSH tunnel**.
1. ✅ **HTTP coordinator service** — `council/net/coordinator_app.py` + SQLite store (`store.py`); persisted ledger/jobs/nodes/telemetry, config-driven, token-auth. Provider-agnostic (env-only → relocatable).
2. ✅ **Networked worker daemon** — `council/net/agent.py`: register / poll / submit / heartbeat; runs on Mac + VPS.
3. ✅ **Stood up the VPS** — `scripts/deploy_vps.sh` + `scripts/vps_run.sh` (survey-first, isolated, reuse Ollama; Finnish worker on `llama3.2`).
4. ✅ **Cross-country council job** — `scripts/cross_country_demo.sh`: Mac (AE) perspectives + VPS (FI) perspective → judged merge; the FI node genuinely diverged (recommended Western Europe vs the Mac nodes' Southeast Asia). Ledger conserved.
5. ✅ **Telemetry/status** — `GET /status` shows both nodes, country, load, ledger conservation.
6. ✅ **Cross-hardware verification recorded** — `scripts/cross_hardware_verify.py` (Mac/Metal vs VPS/CPU on `gemma3:12b`, vs a `gemma3:4b` downgrade). **Finding: they CONVERGE.** Honest cross-hardware floor **0.8473** vs model-downgrade ceiling **0.8495** → a single fixed fuzzy threshold **cannot** separate honest cross-hardware from a downgrade. (Spike-1's clean 0.82 was an artifact of testing on one machine.) See DECISIONS D10 → verification pivots to **quality/judge-based**, not model-identity.

**Done:** ✅ council runs across both machines, ✅ ledger conserves, ✅ telemetry shows both nodes,
✅ cross-hardware number recorded — with a real finding that reshapes verification (folded into M3).

## M3 — Quality, reputation & the live map ⏭ (mostly done)
- ✅ **Reputation/quality tracking** — each owner accrues a rolling mean judge score; exposed in
  `/status`; **functional**: fleet selection prefers higher-reputation nodes (`store.py`). This is what
  D10's "verify on quality, not model-identity" leans on.
- ✅ **Live operator map (v0)** — `GET /dashboard` (`council/net/dashboard.py`): a Leaflet world map
  placing each node by **country** with live load, model, reputation, last-seen, and recent job flow;
  auto-refreshes from `/status`. (Open it via the tunnel: `http://127.0.0.1:8791/dashboard`.)
- ✅ **Honest merge eval** — `scripts/merge_eval.py`: position-swapped + **length-controlled** pairwise
  comparison. **Finding:** the original merge won 3/3 *raw* but only **1/3 length-controlled** — its edge
  was mostly verbosity. Tightening the merge prompt to synthesize *densely with a length cap* moved the
  honest (length-controlled) win-rate to **2/3**, confirming the diversity dividend is real **per word**.
  The merge now errs *short* (~110w vs ~200w single); next refinement: **target** ≈ best-single length,
  not just cap it.

## Future features (interactivity — founder request)
- **Real IP-based geo on the map.** Today nodes self-report `country`; place them by **GeoIP of the
  connection's source IP** instead (works when nodes connect directly rather than via the SSH tunnel,
  which makes everyone look like `127.0.0.1`). Add a GeoIP lookup at registration.
- **Animate work distribution.** Draw live arcs from the asker → assigned worker nodes → judge as a job
  flows, and pulse nodes while they're computing — show the network "thinking" across the world.
- **Operator leaderboard** on the map (top contributors by reputation / jobs helped) — the BOINC-style
  mission/competition layer that recruits supply.

## M4 — Real per-country web-research ✅
`council/research.py`: a node's own agent researches the live web from its egress (DuckDuckGo
metasearch, `region=wt-wt` so **egress IP drives locale** — the moat), SSRF-guarded, 15-min cached,
Wikipedia fallback, **off by default** (`PW_WEB_BACKEND=ddgs` per node). Returns **owned findings**,
never proxied traffic (D4). Verified live (real 2026 results); wired into the agent.

## M5 — Scale & harden ⏭ (security done)
- ✅ **Security/correctness hardening pass** — 25-agent adversarial review → 18 confirmed findings fixed
  (access control, ledger integrity, robustness); see DECISIONS **D11**. Property-tested.
- ◻︎ **systemd service** for the coordinator + worker (survives reboot; replaces tmux).
- ◻︎ More nodes / a third country; GeoIP + work-flow arcs on the map; then **consider GitHub publish**.

## Phase E — The Living Council Map (end-user experience) ✅
The splendid, map-forward product **and** the first real demand test, served at `GET /`.
- ✅ **The app** (`council/net/app.py`): a self-contained Leaflet world map where nodes glow/arc as
  they deliberate; side panel shows the terse TL;DR, **where minds AGREE vs DIFFER (by country)**, the
  **unique** points, a **vs-single-model** compare, and a **▲/▼ "was the council more useful?"** vote.
- ✅ **Structured judge deliberation** (`judge.deliberate`): one blind call → scores + terse merge +
  `{consensus, disagreements, unique}`.
- ✅ **Real accounts + give/take** (`/users`, `X-User-Secret`): asking debits the user's handle;
  contributing their node credits the same handle. Closes the free-string-asker gap.
- ✅ **THE demand metric**: `POST /jobs/{id}/feedback` + `GET /metrics` → **council-vs-single win-rate**
  (the signal this project never had). Verified live cross-country (Mac AE + Helsinki FI).
- ✅ **Always-on**: systemd units (`scripts/install_systemd.sh`) for coordinator + worker; restart-resilient.
- **Open it:** `bash scripts/mac_join.sh` (or any tunnel) → browse `http://127.0.0.1:8791/`.
- ✅ **Honest compare baseline** (`council/net/baseline.py`): the demand metric's "single model" is now
  an INDEPENDENT answer — a frontier API model (`PW_BASELINE_API_KEY`, OpenRouter) or a strong local
  model (`PW_BASELINE_LOCAL_MODEL`, default qwen3:14b) — generated in parallel with the council and
  stored on the job. The old best-council-answer compare only measured merge-vs-ingredient; it remains
  as a clearly-labelled fallback. Pulled forward from Phase F: a win-rate against a weak baseline would
  have misled the first trial. Web research is ON for trial workers (`PW_WEB_BACKEND=ddgs` in
  `mac_join.sh` + the worker unit). Trial runbook: `docs/TRIAL_PROTOCOL.md`.

## First demand trial — DONE (2026-06-10, → D12)
Council 0 / single 7 / tie 3 vs **gpt-5-chat** (two blind position-swapped judges; full table and
excerpts in `docs/TRIAL_RESULTS.md`). The merge lost on substance; the ONLY council edge was
**live web currency** (both research-currency questions went to the council per the frontier-class
judge). Steering: lead with geo-research/privacy/commons, add a ≥14B local anchor mind, deepen
research + show citations; the founder should repeat the protocol in the app for the human signal.

## R18 — FRESHNESS-BIASED RESEARCH (D30, 2026-06-13) — act on the currency-gap finding
The R16 run showed the council had live web but produced stale dates (EU AI Act "2027", FOMC "2023").
R18: `research.extract_date_hint()` + `order_by_recency()` lead the research with the freshest-dated
sources (so they survive the cap + get page-fetched first), gated on `is_time_sensitive(brief)` so
stable-fact queries keep relevance order; the planner/drafter are date-aware ("trust the MOST RECENT,
state the date, don't use training-memory dates"). **Verified FREE (quick-depth, no spend) on the 4
failed questions: 3/4 fixed** — Python ✅ (3.14.6), EU AI Act ✅ (2026-08-02, was 2027), iPhone ✅
(17e, 2026); **FOMC still fails** because the query pulls the SEO-dominant 2023 meeting — recency
ranking can't rescue what search didn't return (a retrieval problem → R19). Review (3 lenses × verify,
17 agents) found 3 real bugs, all fixed: impossible-date regex (datetime-validated now), body-text
topic-year false positives (URL-only bare years), over-recency on static (gated). 162 tests (13 new).
**NEXT R19:** code-level current-year query injection + auto-deeper depth for breaking queries (the
FOMC residual).

## R17 — PERFORMANCE & QUALITY BACKLOG (D29, 2026-06-13) — work the audit's cheap wins
With the eval pair able to measure quality, worked the D26 performance backlog ("do it all and do
anything pending"). Four reversible changes: (1) **dynamic source routing** — `research.route_engines()`
activates the previously-dead arXiv/Wikipedia engines, augmenting (never replacing) the egress-localized
web for academic/definitional queries; env `PW_SOURCE_ROUTING=off`. (2) **Ollama keep-alive** on every
model call (`PW_OLLAMA_KEEP_ALIVE`, default 30m) — kills the 5-30s reload stalls between sequential
calls. (3) **citation-safe merge** — synthesis prompts preserve `[S#]/[L#]` verbatim AND a hard
`_drop_invented_markers` guard strips any fabricated marker (extends D27's honesty to the merge path).
(4) **CI bump** checkout@v5/setup-python@v6/setup-node@v5/Node 24 (ahead of the 2026-06-16 deprecation).
Pre-commit review (2 lenses × verify, 28 agents): most findings confirmed the changes correct, 4
actionable all fixed — **2 missed model-call sites** (`batch.py`, `net/baseline.py` — enumerate EVERY
site, again), the **invented-citation guard**, and encyclopedic over-routing/docs. 149 tests (16 new).
**Deliberately NOT done:** dense-rerank-default over hybrid RRF — contradicts D20's measured choice;
flipping a measured default needs a measured reason. NEXT: founder's call — PyPI publish + recruit
operators (his levers), the founder-gated currency-gap paid run (fill references → `--run`), or more
quality work (merge-prompt density tuning, real benchmark runs).

## R16 — CURRENCY-GAP EVAL (D28, 2026-06-13) — measure the claimed edge
Track B instrument #2, completing the eval pair ("do it all"). `scripts/eval_currency_gap.py` measures
the product's *real* advantage — currency, not raw capability — as an accuracy matrix by currency-window
(static/recent/breaking) × category: council (live web, FREE) vs a BYOK frontier model (parametric, NO
web, PAID via OpenRouter), graded blind 0-10 vs a curated reference; gap is a **paired** mean with
`static` as a fairness control. **Spends nothing by default** — bare run = $0 dry run (validates refs,
estimates cost, prints the command); a paid run needs `--run` + `OPENROUTER_API_KEY` (env only), refuses
all-placeholder refs, caps at 40 questions without `--max`. Pure logic unit-tested; references are a
living human input (static set ships ready, recent/breaking are `VERIFY` placeholders the founder fills).
Pre-commit review (3 lenses × verify, 27 agents): 8 findings were *confirmations the design is correct*,
10 actionable, all fixed — HIGH unbounded-cost foot-gun (→40-question ceiling, aborts pre-network), HIGH
grader self-grading conflict (→loud warning, local grader default), + 8 honesty/clarity (paired column,
small-n ⚠, WITH-web/WITHOUT-web framing, stale-reference disclosure). 133 tests (9 new). **The actual
paid run stays founder-gated — script complete, the spend is his call.** NEXT R17+: the audit's
performance backlog (dynamic source routing, Ollama keep-alive, dense rerank, merge anti-garble).

## R15 — HONEST CITATION-FIDELITY EVAL (D27, 2026-06-13) — measure the core promise
Track B of the D26 audit, instrument #1: does each cited claim actually appear in its source? New
`council/fidelity.py` (pure, dependency-free lexical **grounding floor**, reuses `retrieval.tokenize`)
+ `scripts/eval_citation_fidelity.py` — keyless, no-API-cost, two modes: **A** scores a saved report
by re-fetching its cited URLs; **B** runs fresh and grades each analyst draft against the *exact
extract the model read* (env-gated `PW_CAPTURE_EVIDENCE` capture in `researcher.py`, local-only by
construction). Buckets GROUNDED/WEAK/UNGROUNDED/UNVERIFIABLE; grounded-rate is *of verifiable* claims,
unreachable sources counted separately. Honest by design — a GROUNDED verdict is "not obviously
fabricated", never "verified true". Pre-commit review (4 lenses × adversarial verify, 18 agents)
confirmed 10/14 findings, all fixed: federation-leak guard on capture, multi-digit-only number check
(kills `4.2 million` vs `4,200,000` false positives, keeps catching fabricated stats), symlink/URL/
memory bounds on the attacker-influenceable report path, and sharpened honesty disclosures. 124 tests
(22 new). NEXT — R16: the *currency-gap* instrument (accuracy matrix by currency-window × category,
council vs BYOK frontier) — script buildable now, but its run **spends OpenRouter credit**, so the
paid run stays founder-gated.

## R14 — HARDEN THE INPUTS (D26, 2026-06-13) — back to the adoption engine
Founder chose deepening the published local engine over more marketplace work. A 6-dimension audit
(orchestration / retrieval / synthesis / performance / security / eval) against the founder's own
deep-research report found the top value-to-effort wins were input/output hardening + honest eval
instruments — NOT more SOTA RAG (low leverage on a tool whose edge is currency, not recall), and the
report's whole computer-use half stays out of scope by design. Track A shipped: `sanitize_brief()`
(strip hidden/bidi + length-cap the one user input) at `local.run()` and the MCP boundary
(`_normalize_research_args`, clamps args, no traceback); `batch.py` now spotlights the untrusted item
+ cleans the instruction; `strip_invisible()` scrubs every model-output→report hop (merge/deliberate/
editor/contributions) while preserving markdown + citations. The pre-commit review caught that the
NETWORKED coordinator path was missed → fixed at the `store.create_job` choke point + agent
defense-in-depth + two judge-internal hops. 102 tests (15 new adversarial). NEXT — Track B (R15):
honest eval instruments the audit flagged as most-needed —
`eval_citation_fidelity.py` (does each cited claim actually appear in its source?) and
`eval_currency_gap.py` (accuracy matrix by currency-window × category, council vs BYOK frontier);
scripts built here, the paid/long runs are founder-gated. Then (lower priority): activate dynamic
source routing (arXiv/Wikipedia engines already exist but are never called), Ollama keep-alive to kill
reload stalls, dense-cosine rerank default, merge-prompt anti-garble + citation-preservation.

## R13 — OUT-OF-BAND KEY TRUST (D25, 2026-06-13)
Closes the D23 signing limitation: the asker now pins an operator's signing key locally
(`council/trust.py`, stdlib, ~/.passiveworkers/trust.json 0600) and `pw fetch` verifies against the
PINNED key, not the coordinator-reported one — so a fully hostile coordinator can no longer forge a
delivery for a pinned operator. TOFU on first signed delivery (pinned only after the signature
verifies) + explicit `pw trust add/list/remove`; operators publish a short fingerprint with `pw
fingerprint`; a key mismatch on a pinned operator is refused (re-pin is an explicit human action).
87 tests. Adversarial review caught + fixed 11 findings pre-commit, incl. TWO critical bypasses
(unsigned delivery skipped all checks; a blank operator handle verified against the coordinator's own
key) — verification logic extracted into a unit-tested helper. FEDERATION_V2 authenticity thread
complete (crypto → reputation → key trust). Next: web UI for the marketplace; PyPI publish + recruit
operators; optional fingerprint-on-profile discovery.

## R12 — OPERATOR REPUTATION (D24, 2026-06-13)
Asker ratings for assisted work (`pw rate <job> <score>`, POST /jobs/{id}/rate) feed operator
reputation (unified with council judge-score reputation); `requires.min_reputation` gates offers to
proven operators while newcomers keep ungated offers (cold-start preserved); gate enforced at
offer-listing AND accept; job_view exposes operator + reputation. FEDERATION_V2 trust signal in
place. Next: out-of-band key trust / light PKI (closes the D23 signing limitation); web UI for the
marketplace; PyPI publish + recruit operators.

## R11 — CRYPTOGRAPHIC DELIVERY (D23, 2026-06-13)
Signed deliverables (Ed25519: operator signs, asker verifies) + end-to-end encrypted files (X25519
SealedBox: asker `pw keygen` → encrypt_to → operator seals → coordinator relays ciphertext-only →
asker decrypts). Optional [crypto] extra (PyNaCl), graceful fallback. council/crypto.py +
encrypted-artifact codec. FEDERATION_V2 step 2 done. Honest trust model documented (encryption =
real guarantee; signing binds to operator key, full hostile-coordinator defense needs PKI = future).
Next: PyPI publish + recruit operators; out-of-band key trust / PKI; richer operator UX.

## R10 — FILE DELIVERY (D22, 2026-06-13)
Content-addressed, chunked, integrity-verified file artifacts (council/artifacts.py, stdlib):
operators deliver real files (`pw deliver <task> @file <job>`), askers fetch+verify+reassemble
(`pw fetch <job> <dir>`). Coordinator blob store (dedup, size/count caps, claimant-upload +
asker-download auth). FEDERATION_V2 step 3 done. Next: encryption (asker-held key) + producer
signatures — the [crypto] extra (step 2); then PyPI publish + recruit operators.

## R9 — ASSISTED MARKETPLACE + PyPI-READY (D21, 2026-06-13)
The federation centerpiece: `assisted` human-in-the-loop job type — open offer → operator consent
(`pw tasks`/`accept`) → work done by the human with their own AI → owned deliverable (`pw deliver`)
→ conserved settle. Our code never automates anyone's computer. Endpoints + operator CLI + 39 tests
(ledger conservation, hijack/double-accept guards). Package is PyPI-ready (wheel+sdist, twine PASSED).
Next: founder publishes to PyPI (`twine upload dist/*`); recruit first operators; signed/encrypted
delivery + chunked artifacts (FEDERATION_V2 steps 2-3, security-review gated).

## R8 — BEST-IN-CLASS LOCAL RAG (D20, 2026-06-11)
Hybrid retrieval (dense ⊕ BM25 ⊕ RRF, council/retrieval.py), structure-aware chunking,
parent-window (small-to-big), Anthropic Contextual Retrieval (PW_CONTEXTUAL_CHUNKS), incremental
indexing, opt-in listwise rerank (PW_RERANK). Grounded in a research pass; measured on our own
corpus (scripts/bench_rag.py) — honest finding: a strong local embedder saturates small corpora,
so hybrid is long-tail insurance and chunking/window/contextual are the everyday wins. Skipped
GraphRAG/ColBERT/cloud-rerankers as overkill. Next: federation (FEDERATION_V2 assisted task) + PyPI.

## R7 — COMPLETE THE APP (D19, 2026-06-11)
Local-documents RAG (`pw library`, fully-local embeddings, [L#] citations, --local/--web scope),
MCP server (`pw mcp` — research/library_search/library_add tools for Claude Desktop/Codex), real
pytest suite + GitHub Actions CI, packaging extras (extract/docs/mcp/all), published SimpleQA number.
The single-player engine is now a category of one: private docs + live web + multi-model dissent +
injection-tested security + MCP-callable. Next track: federation/marketplace (FEDERATION_V2 — the
assisted human-in-the-loop task class), and PyPI publish (founder's call).

## R6 — REUSE + TRUST ARCHITECTURE (D18, 2026-06-11)
Embedded trafilatura (real main-content/date extraction, regex fallback); tamper-evident result
digest (FEDERATION_V2 step 1). Decided reuse policy (embed libs / reimplement techniques / MCP
interop — no forks; PRIOR_ART.md), transparency-mandatory (never hidden work), and computer-use =
human-mediated `assisted` handoff (operator uses their own agentic AI under approval — never our
autonomous code). Master structure in docs/FEDERATION_V2.md. Next build: the `assisted` task class
(job type + operator approval UI); then signed/encrypted delivery + chunked artifacts (security-review
gated); then MCP server + local-docs RAG.

## R5 — ECOSYSTEM LESSONS (D17, 2026-06-11)
Full-page evidence (fetch_extract), STORM-lite perspective planner, SearXNG auto-prefer +
docker-compose, DDG backoff, arXiv/Wikipedia routing, SimpleQA bench script. Deliberately not
copied: single-model multi-role designs. Next candidates: local-docs RAG, MCP server.

## SINGLE-PLAYER FIRST (D16, 2026-06-10) — the product to share with the world
The local deep-research engine IS the product: `pw research` (multi-model analysts, live web,
cited report to ./reports/) + `pw serve` (local research desk). Security stance from the
founder's research: no computer-use, sanitizer + spotlighting, zero model tool-privileges,
injection probe in the test suite. The network below is the MULTIPLAYER mode the installed
base grows into. Next: founder reads the founder-grade reports → polish → GitHub (Phase H,
founder's call) → PyPI.

## THE PIVOT (D13, 2026-06-10) — async work marketplace; flagship: Distributed Deep Research
R1 ✅ SHIPPED: typed jobs (`JOB_TYPES`: chat / research_report, per-type price + deadline);
iterative per-country researcher (`council/researcher.py`, multi-round, cited, SSRF-guarded);
editor pass (`judge.compile_report` → one report: exec summary, agree/differ, findings by country
with [S#] citations); app: 🔬 Deep research mode, report rendering, "safe to close" expectation,
`GET /job-types` marketplace catalog. Verified live cross-country.
- **R4 ✅ SHIPPED (D15):** `shard_map` batch jobs — items split across capability-matched
  computers, judge spot-checks quality, assembled deliverable + JSONL copy; capability
  profiles (models/RAM) + `requires` gating; guarded public-URL fetch variant.
- **R2 (next):** founder runs 3 real briefs → per-type win-rate = D14; citation freshness stamps;
  per-type reputation; report quality tuning on real use.
- **R3:** machine-submitter API docs (a computer with credits can already POST work — that IS
  "Upwork for computers"); more job types (batch eval, data-gen → the research-commons north star);
  3rd country; then GitHub publish (Phase H) with the category story.

## Phase F–H (older framing — superseded by R1–R3 above where they conflict)
- **F:** ~~compare vs a hosted frontier model~~ (done early — see Phase E); token-streaming; embeddings agreement view; mobile polish.
- **G:** one-command "contribute your PC" installer; GeoIP + deliberation-arc polish; operator leaderboard; 3rd country.
- **H:** publish to GitHub once the demand metric shows signal.

## Later markets (not yet scheduled)
Research-compute commons (batch/science for under-funded labs) · companies running private
multi-node intelligence · broad individual consumer layer.
