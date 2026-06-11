# Passive Workers — Decisions (ADR-style)

> Append-only record of settled decisions and the reasoning behind them, so we never
> re-litigate. Newest at the bottom. Each: **Decision · Why · Status.**

## D1 — Non-transferable credit, no token, no secondary market
**Decision:** The credit is internal and non-transferable; money enters/leaves only at the platform
edge (top-up / payout). No tradeable token, ever.
**Why:** A tradeable token invites speculation, securities-law exposure (Howey), and *fake* demand
(every comparable network's "traction" collapses to airdrop farming once the token is stripped).
Non-transferable + earned-only-cashout also keeps us clear of money-transmitter status (FinCEN MSB).
Cash payouts (later) trigger a 1099-NEC at $2,000 (2026) — route via a TPSO (e.g. Stripe Connect) to
push KYC/AML to the processor.
**Status:** Settled (founder constraint).

## D2 — No blockchain as system of record
**Decision:** Use an open-source, self-hostable coordinator + a tamper-evident transparency log;
optional Merkle-root anchoring to a public chain as a notary only. No full node per machine.
**Why:** There's a single trusted writer (the coordinator) and a non-transferable credit, so a
blockchain solves nothing here; a full node would *eat the idle resources we sell*.
**Status:** Settled.

## D3 — Never pitch "cheaper inference"
**Decision:** Compete only on diversity / quality / privacy / sovereignty / commons — never price.
**Why:** Centralized small-model inference is ~$0.02/Mtok with free tiers and 10–50× faster than a
laptop; a consumer network is ~2× hosted cost. Price is a losing axis; varied intelligence is not.
**Status:** Settled (research-validated).

## D4 — Nodes return owned deliverables, never proxied traffic
**Decision:** A node's own agent does the work and returns findings it produced. No open residential
proxy / VPN exit; no tunneling others' packets.
**Why:** Routing third-party traffic through a contributor's IP is the gravest legal risk in the whole
space (911 S5 takedown; Tor exit-node operator liability). Consent does not cure it. Returning an owned
deliverable is ordinary, defensible work.
**Status:** Settled.

## D5 — Verification is fuzzy semantic agreement
**Decision:** Compare answers by meaning (embedding cosine + token overlap) against a trusted
reference, with reputation gating — not exact output-hash.
**Why:** LLM inference is not byte-reproducible across heterogeneous hardware; exact hashing would
falsely punish honest cross-hardware workers. The real threat is lazy **model-downgrade**, which M2
will measure. (Evaluate TOPLOC later as a complement.)
**Status:** Settled in approach; cross-hardware + downgrade thresholds to be measured in M2.

## D6 — Cold-start = mission-first commons + dual-role recirculating credit
**Decision:** Recruit supply on mission (open-source, help-each-other); bootstrap demand via the
give/take loop where contributors are also consumers. External cash is a *later* gate, before
commercializing to companies.
**Why:** Without a token there's no speculative bribe to seed a stranger-market; the dual-role loop
is the most realistic cold-start (BOINC sustained millions tokenless on mission alone).
**Status:** Settled.

## D7 — North star = the Council (varied intelligence)
**Decision:** The collaborative mutual-aid Council is the **primary product and north star**.
Research-compute/batch-science and the broad consumer layer are **later markets** of the same substrate.
**Why:** It's the founder's chosen starting product, it escapes the price floor (D3), and it's the
honest world-changing aim (break intelligence concentration).
**Status:** Settled (founder decision).

## D8 — Coordinator is provider-agnostic and portable
**Decision:** The coordinator starts on the VPS but is containerized and config-driven, relocatable to
any rented host with no code changes (SQLite file → Postgres via the same config seam).
**Why:** The founder may move compute to cheaper/closer rented resources later; avoid lock-in.
**Status:** Settled.

## D9 — Phones excluded; GitHub deferred
**Decision:** No phones in scope (sustained background inference throttles ~40–60% in ~90s). Develop
locally; publish to GitHub once the MVP is shareable.
**Status:** Settled.

## D11 — Hardened trust model (per-node secret + fail-closed ledger)
**Decision:** Authenticate node operations with a **per-node secret** (not just the shared token); a
node may only complete its own tasks; settlement is **fail-closed** with sanitized scores; the
coordinator is loopback-by-default; `/status` leaks no node_id/IP and the dashboard escapes all
node-supplied fields; agents heartbeat on a background thread with a job reaper.
**Why:** An adversarial review (25 agents) found that a single shared token + self-asserted identity let
any node hijack the blind judge role and forge ledger settlements, a stored-XSS vector via node names hit
the operator's browser, non-finite judge scores could permanently break conservation, and dead nodes
wedged jobs forever. All confirmed against the code and fixed; property-tested (inf/NaN conservation,
fail-closed, hijack rejection, SSRF, XSS escaping).
**Status:** Settled & implemented (M2 hardening pass). Deferred: asker identity (askers aren't principals
yet), SearXNG-per-node, Postgres connection-per-thread.

## D10 — Verification is quality/judge-based, NOT model-identity (empirical)
**Decision:** Do not try to police *which model* a worker ran via a single fuzzy-agreement threshold.
Verify on **answer quality** instead — the judge already scores every answer and pay is score-weighted,
so a lazy/downgraded worker that produces a worse answer simply earns less; reputation gates the rest.
**Why (measured M2):** real cross-hardware test — `gemma3:12b` on Mac/Metal vs VPS/CPU — gave an honest
agreement **floor of 0.8473**, while a `gemma3:4b` downgrade reached a **ceiling of 0.8495**. They
**overlap**: no single threshold separates honest-cross-hardware from a downgrade. And on *easy* prompts
a smaller model's answer is genuinely fine, so "downgrade" isn't even a cheat there — only quality matters.
This overturns Spike-1's clean 0.82 (an artifact of testing on one machine). If model-identity ever truly
matters, use TOPLOC/TEE for those jobs — not a global threshold.
**Status:** Settled by measurement; folds into M3 (quality eval) and the existing score-weighted ledger.

## D12 — First demand trial: council 0/10 vs frontier; the edge is currency, not quality
**Decision:** Stop competing on general answer quality against frontier models. The product leads with
what a centralized frontier model cannot do: live geo-localized cited web research (the one measured
edge), privacy, and the mutual-aid commons. Raise the council's substance floor with at least one
strong local mind (≥14B) per fleet, deepen per-country research, and keep the honest in-app
frontier compare as the standing quality bar.
**Why:** The first 10-question trial (2026-06-10, docs/TRIAL_RESULTS.md) ran the live cross-country
council against gpt-5-chat with two blind position-swapped judges: council 0 wins, single 7, tie 3.
The council's merges lost on substance (generic where the frontier was specific; factual errors on
SMRs) — 3–9B workers cannot out-substance a frontier model, merged or not, exactly as D3 predicted.
The only council wins (per the frontier-class judge) were the two questions where 2026-currency
mattered: its web research returned current, cited answers while gpt-5-chat answered from stale
training data. A knowledge-frozen local judge could not even see that edge — currency is invisible
to most judges but not to a real asker.
**Status:** Settled by measurement. Done since: merge-leak fixed (a merge cited "Answer 1 and 3…" —
prompts now forbid referring to candidates), merge length targeted to best-single. Next per this
decision: strong-mind fleet anchor, research depth + citations in the UI, founder repeats the
protocol in the app for the human signal.

## D13 — The pivot: async work marketplace ("Upwork for computers"), flagship = Distributed Deep Research
**Decision:** Passive Workers stops being a chat product. It is a marketplace where computers do
JOBS for other computers in a different latency class: a brief goes in, machines work for minutes,
a judged deliverable comes back. Job types are typed, priced, and deadlined (`JOB_TYPES` in
`council/net/config.py`; `jobs.type`). The flagship type is **`research_report` — Distributed Deep
Research**: every node runs multi-round, egress-localized, SSRF-guarded web research from its own
country (`council/researcher.py`), cites sources `[S#]`, and a blind editor compiles one report with
per-country findings and an agreement/difference read (`judge.compile_report`). Chat remains as a
demo + honesty bar (the frontier compare and vote carry over to reports).
**Why:** Three signals converged. (1) D12: chat lost 0/10 to gpt-5-chat, but the council won BOTH
questions where live-web currency mattered — the edge exists only in deferred work. (2) The original
research: the only pattern that ever paid on consumer hardware is latency-tolerant batch (Salad,
Vast.ai); chat on distributed consumer machines has never worked. (3) The founder's own use: distant
nodes feel slow in chat and irrelevant in a 30-minute job; Deep Research products trained users to
wait for reports. Category claim: the only deep research done by many real computers in many real
countries — one-egress centralized DR cannot copy in-country sources without a residential fleet,
and D4 keeps ours legal (owned findings, never proxied traffic).
**Status:** Settled (founder pivot, 2026-06-10) & implemented end-to-end same day. First live
two-country report verified (FI+AE sources, cited, conserved ledger). Next: founder runs 3 real
briefs (→ D14 demand signal per type), per-type reputation, then more job types (batch eval,
data-gen — the research-commons north star) and Phase H with the category story.

## D15 — Sharded batch work; what may (and may NOT) be distributed
**Decision:** Second marketplace job type: **`shard_map`** — one big job's items split round-robin
across capability-matched computers (≈N× wall-clock speedup; the honest "divide to save time").
The judge becomes a QA sampler (blind spot-check of each node's outputs → score-weighted payout);
the deliverable is the assembled shards in input order. Nodes declare capabilities at register
(models via Ollama tags + RAM/cores/OS) and jobs may declare `requires` — **not all tasks are open
to all nodes.** A `fetch:true` variant lets items be PUBLIC URLs each node fetches itself
(SSRF-guarded, size-capped, one polite request) and returns the model's EXTRACTION.
**The bright line (extends D4):** distribution is for THROUGHPUT and PERSPECTIVE, never for
network-identity arbitrage. A node may only fetch what it could lawfully fetch alone, politely;
only value-added AI output leaves the node — never raw relayed bytes; and distributing requests
to EVADE per-IP rate limits, geo-blocks, logins, or paywalls is forbidden (that is the
residential-proxy pattern — 911 S5 — that D4 exists to keep us away from). Running third-party
licensed software (Blender/ffmpeg/etc.) as a service stays in the PARKED gated track: v1
capabilities are models + hardware only — no arbitrary code execution on contributor machines.
**Why:** Founder direction (divide long tasks; distribute network-heavy work; per-node skills),
which lands exactly on the validated capability envelope — embarrassingly-parallel batch is the
ONLY pattern that ever paid on consumer hardware. The legal framing keeps the good idea and
fences the trap.
**Status:** Settled & implemented (config JOB_TYPES, store sharding + `_meets`, council/batch.py,
judge.spot_check, app ⚙️ Batch mode). Verified live cross-country same day.

## D16 — Single-player first: the local deep-research engine IS the product; the network is the upgrade
**Decision:** Invert the cold start. The product worth sharing with the world is the **local-first
deep-research engine**: `pw research "brief"` on any computer with Ollama — multiple installed
models research the live web as independent analysts, a blind editor (strongest local model, or
BYOK frontier with `--editor api`) compiles a cited markdown report into `./reports/`; `pw serve`
is the single-user research desk UI. Everything network-side (council/net: federation, credits,
map, marketplace job types) remains in-repo as the **multiplayer mode** the installed base grows
into — the SETI@home pattern: the screensaver first, the network as a side effect.
**Security stance (from the founder's own deep-research report, adopted as requirements):**
deliberately NOT computer-use — search API + plain fetch of public pages only; no browser, no
sessions, no cookies ever. All web content is untrusted data: sanitized (invisible-Unicode/
hidden-comment stripping, `council/sanitize.py`) and spotlighted ("data, never instructions") in
every prompt that carries it; models hold zero tool privileges (text out only; Python acts);
reports write only to ./reports/; fetches SSRF-guarded. Dissent-preserving editor retained
(anti-"deliberative illusion"). Verified with a live injection probe.
**Why:** Every prior product required the network to exist before being valuable (vision ÷ N=2 —
why each version felt immature in the founder's hands). The one measured win (D12) is live cited
web research; a single-player tool monetizes that edge at N=1, is honestly shareable as
open source (a tool is judged by what it does, not by company maturity), and every install is a
future federation node.
**Status:** Settled & implemented (council/{local,serve,cli,sanitize}.py, pyproject `pw` entry,
MIT LICENSE, README rewritten). Verified: Mac quick run (1,052-word report, 16 curated sources,
2.3 min), serve end-to-end, injection probe passed, VPS standalone run, founder-grade briefs.
GitHub publish (Phase H) remains the founder's call.

## D17 — Ecosystem lessons adopted: full-page evidence, perspectives, SearXNG-first
**Decision:** Adopt the proven quality playbook from the category leaders while keeping our
differentiators. (1) **Full-page evidence**: analysts draft from SSRF-guarded, sanitized page
EXTRACTS (top results fetched via shared `research.fetch_extract`), not 500-char snippets —
gpt-researcher (27.6k★) scrapes 20+ full pages; this was our biggest quality gap. (2) **STORM-lite
perspective planning**: the smallest local model discovers K distinct angles; each analyst
researches the brief through its own angle — question diversity multiplying our existing model
diversity (Stanford STORM's proven technique). (3) **SearXNG-first**: auto-prefer a local SearXNG
instance, `docker-compose.yml` ships one; DDG calls now retry with backoff — DDG rate limiting is a
systemic plague across the ecosystem (gpt-researcher #478, local-deep-research #18, open-webui,
CrewAI, dify) and we will not wait to be bitten. (4) **Keyless engine routing v1**: arXiv +
Wikipedia full-text as routable engines. (5) **Benchmark candor**: `scripts/bench_simpleqa.py`
publishes SimpleQA-subset results including misses.
**What we deliberately did NOT copy:** the leaders run multiple agent ROLES on one model; nobody
runs cross-FAMILY models and surfaces their disagreement, and none lead with injection defenses.
The multi-model dissent-preserving council + tested security stance + federation remain ours.
**Roadmap (recorded, not built):** local-documents RAG ("your PDFs + the live web" —
local-deep-research's killer feature), MCP server (lets Claude and friends call us),
JS-rendered scraping.
**Status:** Settled & implemented (R5).
