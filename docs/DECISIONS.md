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

## D18 — Reuse policy, transparency-mandatory, and computer-use = human-mediated handoff
**Decision (three settled points):**
1. **Reuse policy.** (a) EMBED mature single-purpose libraries as clean pip deps with attribution,
   never forks — first embed: trafilatura (Apache-2.0) for main-content/date extraction. (b) LEARN
   techniques and REIMPLEMENT in our idiom from the agent frameworks (gpt-researcher Apache-2.0,
   local-deep-research MIT, STORM MIT) — do NOT vendor their LangChain/LangGraph orchestration; it
   would explode our dependency surface and break our auditable, no-tool-privilege, no-computer-use
   promise, and the orchestration is our differentiator. (c) INTEROP via MCP (roadmap), not absorption.
   Credits in docs/PRIOR_ART.md.
2. **Informed, tiered consent (never deception).** An operator gives INFORMED consent to a *class*
   of work (e.g. "run research / inference tasks"); thereafter individual tasks of that class run
   without a per-task dialog — the legitimate volunteer-compute pattern (BOINC/SETI@home), and what
   the founder proposed: don't bother the operator for every unit, but they always know the work
   class, can see logs, and can stop. Sensitive classes (licensed-software, computer-use,
   heavy-compute) escalate to explicit per-task human approval with a brief and **minimal context**
   (privacy for both operator and asker). The ONLY thing forbidden is DECEPTION — running work an
   operator hasn't been told the nature of, or misrepresenting it. That (not tiered consent) is the
   residential-proxy/botnet line (CFAA, 911 S5). The operator can always audit and revoke.
3. **Computer-use = human-mediated handoff, never autonomous agent code (founder's resolution).** When
   a task needs a real computer driven (browser, licensed software), it is handed to the human
   operator, who completes it via their OWN agentic AI (Claude, Codex) or by hand, under approval and
   with a bounded brief, returning the owned deliverable. Our code never automates anyone's machine,
   holds no sessions/credentials. This dissolves the CFAA/proxy exposure and the Task-Injection attack
   surface entirely, and becomes the `assisted` marketplace job type (docs/FEDERATION_V2.md). A
   sandboxed public-only headless JS reader remains a possible minor, far-later, gated capability — not
   a priority, since the human-mediated path covers the hard cases.
**Why:** The founder asked whether to fork the leaders and whether to pursue computer-use/distributed
downloads with work hidden from operators. Two of those re-enter settled legal traps; this decision
keeps the legal, valuable core of each and records the elegant human-mediated resolution.
**Status:** Settled. Implemented this round: trafilatura embed, tamper-evident result digest
(FEDERATION_V2 step 1), PRIOR_ART + FEDERATION_V2 docs. The rest is designed and gated.

## D19 — Local-documents RAG + MCP server: the single-player engine becomes a category of one
**Decision:** Ship two roadmap features that, combined with what we already have, make the tool
unique. (1) **Private-document library (local RAG)**: `pw library add` indexes PDFs/Word/text,
chunked + embedded locally via Ollama `nomic-embed-text` into ~/.passiveworkers/library.db (SQLite +
numpy cosine — no heavy vector DB); the research engine draws on these documents ALONGSIDE the live
web (`--local`/`--web`/both), citing docs as `[L#]` vs web `[S#]`. Fully local, keyless, nothing
uploaded — reinforces the privacy promise. (2) **MCP server** (`pw mcp`, optional `[mcp]` extra):
exposes `research`/`library_search`/`library_add` over stdio so Claude Desktop / Codex / any MCP
client calls the engine as a tool — the interop play (D18) and the founder's "use your own agentic
AI" worldview realized. Plus: real pytest suite (locks the `_extract_json` bug class + sanitizer +
digest + RAG), GitHub Actions CI, packaging extras (extract/docs/mcp/all), publish-ready pyproject.
**Why:** Founder mandate to complete the app and make it unique. No other tool fuses private docs +
live web + multi-MODEL dissent-preserving council + injection-tested security + MCP-callable. Each
piece exists somewhere; the combination is ours. Single-player first remains the strategy (D16);
federation/marketplace (FEDERATION_V2) is the deliberate next track.
**Status:** Settled & implemented (council/library.py, council/mcp_server.py, tests/, CI, pyproject
extras). Verified: local-only RAG research cites [L#] from a private doc; MCP server exposes 3 tools;
20 unit tests green; clean-venv `pip install` exposes `pw`.

### D19 addendum — adversarial review pass (R7)
A workflow review (3 dimensions × adversarial verify) surfaced 12 findings; 9 confirmed and fixed
before publish: (sec) MCP `library_add` arbitrary-path read → `PW_LIBRARY_ROOTS` confinement
(default home) + symlink skip; unbounded ingest → per-file/size/count/total caps; `library_search`
output now sanitized+spotlighted. (correctness) `[L#]` dedup mismatch → one `[L#]` per document so
prompt markers and the listing align; `fix_dangling_citations` generalized to `[SL]`; cite
instruction conditional on which sources exist. (integration) MCP stdout corruption → progress to
stderr; `numpy` promoted to a core dependency; federation worker pins `scope="web"` (no operator
library access). Regression tests added (confinement, dangling-`[L#]`). 25 unit tests green.

## D20 — Best-in-class local RAG: hybrid retrieval + structure-aware chunking + contextual retrieval
**Decision:** Upgrade the private-library RAG to the current (2026) state of the art, grounded in a
research pass and **measured on our own corpus** (not vendor numbers): (1) **Hybrid retrieval** —
dense cosine ⊕ BM25 lexical, fused by Reciprocal Rank Fusion (k=60), top-50/retriever
(`council/retrieval.py`, pure-Python, zero new deps); catches exact terms (names/codes/numbers)
dense can miss. (2) **Structure-aware chunking** — split on headers then recurse
paragraph→line→sentence→word to ~2000 chars; never straddle a section (replaced the old whitespace-
collapsing 1400-char slicer, the measured weak link). (3) **Parent-window expansion** (small-to-big)
via the existing `ord` column — retrieve precise chunks, feed neighbor-expanded context. (4)
**Contextual Retrieval** (Anthropic technique, flag `PW_CONTEXTUAL_CHUNKS=1`): a small local model
writes a situating blurb prepended before embedding/BM25; title is always prepended free; the blurb
never leaks into the displayed `[L#]` quote (separate `text` column). (5) **Incremental indexing** —
content-hash skip-unchanged. (6) **Opt-in listwise rerank** (`PW_RERANK=1`, zero-dep local-LLM).
**Measured honestly (scripts/bench_rag.py):** on small clean corpora a strong local embedder
(nomic-embed-text) already saturates retrieval (dense 7/7 = hybrid 7/7), including exact-code and
buried-term cases — so hybrid is robustness insurance for the long tail, and the everyday wins are
the chunker, parent-window, and contextual blurbs. We publish this rather than a vendor "+35%."
**Skipped as overkill for a lean local tool:** GraphRAG, ColBERT/late-interaction, cloud rerankers,
trusting vendor benchmark numbers.
**Status:** Settled & implemented (council/retrieval.py, council/library.py; scripts/bench_rag.py;
tests). Default path stays fast (contextual + rerank are opt-in).

### D20 addendum — adversarial review pass (R8)
A workflow review (3 dimensions × adversarial verify) surfaced 13 findings; 9 confirmed and fixed
before commit: (HIGH) `BM25.top()` recomputed the score vector inside the sort key → O(n²); now
computed once. (med) char overlap inflated chunks past the size budget → `_split_recursive` now
targets `CHUNK_CHARS − OVERLAP` so stored chunks stay ≤ budget. (low) recursive split dropped
sentence punctuation at flush → separator re-appended. (med/low security) rerank candidate text and
the situating-blurb document are untrusted → both now `spotlight()`-wrapped before the LLM call.
(low) query-time rebuilt the whole matrix+BM25 each call → cached on a (count, max-id) fingerprint
(helps the 3-analysts/run and serve/MCP loop). (low) mixed pre/post-contextual embedding spaces →
one-line re-index hint. (verified safe) the context blurb never leaks into `[L#]` quotes — locked
with a test. 34 unit tests green.

## D21 — The assisted task class: human-in-the-loop marketplace ("computers work for each other")
**Decision:** Ship the federation centerpiece (FEDERATION_V2 step 0) — a new job type **`assisted`**.
It is an OPEN offer (not pre-assigned): an asker posts a brief + **bounded context**; any consenting,
capability-matched operator sees it (`pw tasks`), gives **informed consent** by claiming it
(`pw accept`), does the work **themselves** — with their own agentic AI (Claude, Codex) or by hand —
and delivers the **owned** result (`pw deliver`). Our software NEVER automates the operator's
computer; the human is always the agent (D18). Settlement: asker debited the pool, operator credited
the pool, **no judge, conserved** — money only at the edges (D1). Endpoints `GET /tasks/offers`,
`POST /tasks/{id}/accept|deliver` (per-node-secret auth, atomic claim under the store lock); the
agent daemon never auto-claims assisted tasks; the reaper expires unclaimed offers after a long
(24h) human-paced deadline with no ledger impact (debit happens only at delivery).
**Why:** This is the founder's own resolution of "computer-use" (human-mediated handoff) and the
heart of "Upwork for computers" — legally clean (no autonomous automation, no proxied traffic,
consented + transparent), and it makes the network a marketplace, not just a local tool. The
single-player engine (D16) remains the adoption engine that brings the operators.
**Status:** Settled & implemented (store assisted lifecycle, coordinator endpoints, `council/operator.py`
CLI, JOB_TYPES["assisted"]). Verified end-to-end through the real HTTP API: offer→consent→accept→
deliver→settle, conserved, with hijack/double-accept protection; 39 unit tests green; package
PyPI-ready (wheel + sdist, twine check passed).

### D21 addendum — adversarial review pass (assisted)
A workflow review (3 dimensions × adversarial verify) surfaced 10 findings, clustering into 4 real
fixes, all applied before commit: (HIGH) a reaped/expired offer could still be settled on a late
delivery (asker charged for a lapsed offer) → `deliver_assisted` now requires job status
`assisting`. (the big one) **credit escrow**: the asker's reward is HELD in an internal non-granted
escrow account at offer creation (`ledger.hold`), released to the operator on delivery
(`ledger.release`), and refunded by the reaper on expiry (`ledger.refund`) — so an operator who did
the work can't be left unpaid by the asker spending elsewhere, and conservation is exact across
save/reload. (med) self-deal blocked — an asker can't accept their own offer. (low) `jobs_helped`
was triple-counted (settle_job worker+judge same account + a manual ++) → escrow path counts once,
and `settle_job` no longer double-counts when judge is also a payee. (low) capability re-checked at
accept, not just in the offer filter. Escrow account hidden from `/status`. +6 regression tests
(42 total green); HTTP e2e + reload conservation verified.

## D22 — Content-addressed file delivery: operators return real files, integrity-verified
**Decision:** Marketplace deliverables can now be FILES, not just text (FEDERATION_V2 step 3).
`council/artifacts.py` (stdlib only) splits a file into 256 KiB chunks, hashes each (sha256 = the
chunk's address), and records a manifest {name, size, chunks:[hashes], root} where root = sha256 of
the ordered chunk hashes (a flat Merkle root). The coordinator stores chunks as opaque
content-addressed blobs (`blobs` table; dedup by hash; per-job count cap; per-chunk size cap; the
store re-verifies hash==content on upload). Auth: only the claiming operator may upload
(`assisted_claimant` check), only the job's asker may download (job-scoped `get_blob` + asker check).
The receiver verifies every chunk against its hash AND the manifest root before writing, reduces the
filename to a basename inside the output dir (no traversal), and aborts on any mismatch — a
corrupted/swapped/missing chunk never reaches disk. Operator: `pw deliver <task> @file <job>`;
asker: `pw fetch <job> <dir>`.
**Why:** "Continue" down FEDERATION_V2 — an operator who renders an image or processes a dataset
could previously only return a path string. This is the founder's "split files / share files between
computers" idea, done with integrity by construction. Encryption (asker-held key) + producer
signatures are the clean follow-on (the [crypto] extra, FEDERATION_V2 step 2) — content-addressing
already gives tamper-evidence.
**Status:** Settled & implemented. Verified end-to-end through the real HTTP API: chunk upload (auth +
hash-checked) → manifest delivery → asker fetch → verify → reassemble (bytes identical); cross-asker
and non-claimant access blocked; tamper/doctored-manifest/path-traversal rejected. 49 tests green.

### D22 addendum — adversarial review pass (file delivery)
A workflow review (3 dimensions × adversarial verify) surfaced 16 findings; the real ones fixed
before commit: (CRITICAL) operator uploaded binary chunks with `Content-Type: application/json` →
FastAPI 400; now sends `application/octet-stream`. (HIGH) content-addressed dedup with a hash-only
PK + job-scoped reads silently stranded a second asker → composite `PRIMARY KEY(hash, job_id)` so
each job keeps its own copy; put_blob now confirms the row is stored (no false success). (HIGH)
full request body buffered before the size cap → Content-Length 413 guard before trusting the body.
(HIGH) blobs never reclaimed → reaper deletes blobs of terminal jobs past a retention window
(PW_BLOB_RETAIN_S, default 7d). (MED) operator could be paid before all chunks uploaded →
deliver_assisted verifies `blobs_present` for every manifest chunk before settling. (MED) per-job
byte cap (200 MB) replaces the count cap. (MED) path-escape guard now segment-aware. (LOW) explicit
tagged artifact discriminator (no confusing JSON text for a file); manifest validates size + hex
chunk hashes; empty file valid. Verified end-to-end (octet-stream upload, payment gate, 413,
conservation). 52 tests green.

## D23 — Cryptographic delivery: signed deliverables + end-to-end encrypted files (optional)
**Decision:** Two cryptographic guarantees on top of content-addressed delivery (D22), both via the
optional `[crypto]` extra (PyNaCl/libsodium) with graceful fallback to D22 integrity when absent.
(1) **Signing (Ed25519)**: an operator signs (exactly the stored bytes) with a private key the
coordinator never sees (`council/crypto.py`); `pw fetch` verifies the signature AND that the signer
key equals the key the claiming operator REGISTERED (`registered_sign_pub` from the node record),
aborting on either mismatch. So a delivery can't be signed by an arbitrary key, and content tampering
is detected. (2) **Encryption (X25519 SealedBox)**: the asker publishes a
public key with the job (`encrypt_to`, via `pw keygen`); the operator seals each file chunk to it;
the coordinator stores ONLY ciphertext (verified: a stored blob ≠ plaintext); the asker unseals on
fetch. Ciphertext hash is verified BEFORE decryption. Keys persist per identity (0600).
**Honest trust model (documented, not overstated):**
- Encryption is a REAL confidentiality guarantee even against a hostile coordinator — it only ever
  holds ciphertext and the asker's public key (public by design).
- Signing binds the deliverable to the operator's REGISTERED key and detects tampering. Its
  limit: the coordinator stores that registered key, so a fully hostile coordinator that rewrites the
  node record + content + signature together needs out-of-band key trust to defeat — **now addressed
  in [D25] via TOFU + explicit key pinning** (`pw trust`), which verifies against a pin the
  coordinator can't change. (Encryption's `encrypt_to` has the symmetric caveat: a hostile
  coordinator could substitute its own pubkey at job-post; mitigation is the asker publishing their
  key out-of-band — noted.)
**Why:** "Continue" — FEDERATION_V2 step 2, the security groundwork for operators exchanging real
files. Confidentiality + authenticity are what make a marketplace of strangers' computers trustworthy.
**Status:** Settled & implemented. Verified end-to-end through the real API (encrypt-to-asker,
ciphertext-only storage, signature verify, decrypt to original bytes, wrong-key rejected, conserved).
57 tests green (crypto tests skip cleanly without the extra).

### D23 addendum — adversarial review pass (crypto)
A workflow review (3 dimensions × adversarial verify) surfaced 13 findings; the real ones fixed
before commit: (HIGH) operator signed the FULL deliverable but sent/stored a `[:200000]` truncation
→ honest >200k-char deliveries verified as INVALID; now signs exactly the bytes sent. (HIGH) the
signature was self-referential (asker never checked the signer key against the operator's REGISTERED
key) → job_view now exposes `registered_sign_pub` and `pw fetch` rejects a signer ≠ the claiming
operator's registered key. (HIGH) encryption downgrade — an asker who required `encrypt_to` would
silently accept a plaintext deliverable → fetch now refuses a non-encrypted file when encryption was
required. (MED) `operator.json` (a bearer node-secret) now chmod 0600; D23 prose corrected to match
the (now-real) binding. (LOW) strict base64 (`validate=True`); crypto funcs raise a clear error /
verify() returns False when PyNaCl is absent; key files created owner-only (no TOCTOU window). 60
tests green; large-text parity + registered-key binding verified end-to-end.

## D24 — Operator reputation from asker ratings; reputation-gated offers
**Decision:** Close the assisted quality loop — the marketplace's trust signal. After an assisted
job is delivered, the **asker rates it 0-10** (`POST /jobs/{id}/rate`, `pw rate <job> <score>`); the
rating feeds the operator account's `quality_sum/quality_n` — the SAME reputation signal council
nodes earn from blind judge scores (`avg_quality`). One rating per job (idempotent: the assisted
task's `score` stays NULL until rated). A job may set `requires.min_reputation`; offers and accept
then admit only **proven** operators (`quality_n>0` AND `avg_quality>=min`), while newcomers keep
taking ungated offers so cold-start isn't blocked. `job_view` exposes the claiming operator + their
reputation + ratings count; the gate is enforced at BOTH offer-listing and accept (no bypass).
**Why:** "Continue" — a marketplace of strangers' computers needs a way to know who does good work.
Previously an assisted operator who delivered garbage still got paid with no signal; now bad work
lowers reputation and good work unlocks higher-trust (gated) offers. Unifies council + assisted
reputation on one account metric.
**Status:** Settled & implemented. Verified end-to-end (rate → reputation; idempotent; non-asker
blocked; min_reputation hides offers from under-rep AND unrated operators; newcomers still see
ungated; conserved). 70 tests green.

### D24 addendum — adversarial review pass (operator reputation)
A parallel-reviewer workflow (each finding independently re-verified) surfaced four real issues;
all fixed before commit, each with a regression test:
- **Reputation farming (high).** Anyone could mint throwaway asker handles, give an operator a
  fresh 50-credit starter balance's worth of nothing, and rate them 10 repeatedly to fabricate
  reputation. Fix: a rating now moves the operator's *reputation metric* only if the rater has
  **independent earned standing** (`lifetime_earned > 0` — they've actually helped someone, the
  give/take principle) **and** at most **once per `(asker, operator)` pair** (new `rater_pairs`
  table). The rating is always *recorded* on the task; this only governs whether it counts toward
  the gate. (`test_unearned_rater_does_not_move_reputation`, `test_per_pair_rating_counts_once`.)
- **Gate fail-open on a malformed threshold (high).** A non-numeric / NaN `min_reputation` made the
  capability comparison throw or compare falsely, which could silently *admit* unqualified
  operators. Fix: `_meets_reputation` **fails closed** — a non-numeric or non-finite threshold
  admits no one; a genuinely absent gate still opens to everyone (cold-start preserved).
  (`test_gate_fails_closed_on_bad_value`.)
- **Malformed gate accepted at creation (medium).** A fat-fingered `min_reputation` (string, NaN,
  out of 0–10) used to create a permanently un-takeable offer that still escrowed the asker's
  credit. Fix: `_create_assisted` validates the threshold up front and fails the job with a clear
  error *before* holding escrow. (`test_malformed_min_reputation_rejected_at_creation`.)
- **Unknown node could bypass the capability gate at accept (medium).** `accept_assisted` only
  checked `_meets` when the node was registered, so an *unregistered* node slipped past a
  capability requirement. Fix: when an offer sets requirements, an unknown node is ineligible
  (cannot prove capability); a no-requirement task is still acceptable by any node. (Covered by the
  existing capability-gate + accept lifecycle tests.)

## D25 — Out-of-band operator key trust (TOFU + pinning): closes the D23 signing gap
**Decision:** Give the asker a root of trust the coordinator does not control. D23's signing bound a
deliverable to the key the **coordinator** reported for an operator (`registered_sign_pub`), so a
fully hostile coordinator could rewrite the node record + content + signature together and still
"verify." D25 closes that: the asker maintains a local **pin store** (`~/.passiveworkers/trust.json`,
0600, pure stdlib — `council/trust.py`) mapping an operator handle → its Ed25519 signing key, and
`pw fetch` verifies every delivery against the **pinned** key, not the coordinator-reported one.
- **TOFU** (SSH `known_hosts` model): on the first signed delivery from an operator, pin the key —
  but only **after** the signature verifies (we never pin a key taken from an invalid signature) —
  and warn that first contact is unverified until the fingerprint is compared out of band.
- **Explicit pin**: the operator runs `pw fingerprint` (prints their signing pubkey + an 80-bit
  base32 `PW-XXXX-XXXX-XXXX-XXXX` fingerprint) and shares the key over a trusted channel; the asker
  runs `pw trust add <operator> <pubkey>` (also `trust list` / `trust remove`).
- **Mismatch → refuse**: if a pinned operator presents a different key, fetch aborts and shows both
  fingerprints; re-pinning a rotated key is always an explicit, human-verified action (never
  automatic), so a coordinator can't silently rotate a pinned operator's key.
**Result:** for any operator the asker has pinned out of band, signing now defeats even a fully
hostile coordinator — it can neither present a different key (refused at `classify`) nor forge a
signature under the pinned key (verification fails on tampered content). TOFU narrows, but does not
eliminate, the window for operators not yet pinned — documented honestly, not overstated. A full
directory PKI remains out of scope (and unnecessary for a commons where operators can publish a
fingerprint on a profile/README).
**Why:** "Continue" — completes the FEDERATION_V2 trust thread (R11 crypto → R12 reputation → R13 key
trust). The remaining D23 caveat was the one piece preventing the marketplace's authenticity
guarantee from being whole.
**Status:** Settled & implemented. `council/trust.py` + `pw fingerprint` / `pw trust` + fetch verifies
against the pin. 87 tests green.

### D25 addendum — adversarial review pass (out-of-band key trust)
A workflow review (3 lenses × adversarial verify, 14 agents) surfaced 11 confirmed findings — two of
them *critical bypasses of the very guarantee R13 claims*. All fixed before commit, with regression
tests, and the security-critical logic was extracted into a unit-testable helper
(`operator._verify_delivery_signature`) so it no longer hides behind HTTP:
- **(CRITICAL) Unsigned delivery bypassed everything.** `fetch` gated all verification on
  `if sig and signer:`, so a hostile coordinator could just *strip the signature* and ship tampered
  content. Fix: a **pinned** operator MUST sign — an unsigned delivery from them is refused.
- **(CRITICAL) Blank operator handle → verify against the coordinator's own key.** With `operator=""`
  the pin lookup missed and verification fell back to the coordinator-supplied key (self-consistent,
  meaningless). Fix: a signed delivery with no operator handle has no trust anchor → refused.
- **(HIGH) Silent downgrade with no crypto extra.** A signed delivery was *accepted unverified* when
  PyNaCl was absent. Fix: refuse (exit 2), matching the encryption path — never accept unverified.
- **(HIGH) Missing operator-identity guard.** `accept_assisted` now rejects an empty owner so every
  claim carries a pinnable identity (defense-in-depth for honest coordinators).
- **(HIGH) Insecure save fallback / corrupt-store data loss.** `trust.json` now writes **atomically**
  (temp + `os.replace`, 0600 from creation, fallback still chmods); an unreadable store is **moved to
  `.corrupt` with a warning** instead of being silently overwritten (which would drop every pin). Same
  chmod-fallback hardening applied to `crypto.py`.
- **(HIGH/MED) Swallowed TOFU-pin errors + test gap.** The broad `except: pass` is gone; a failed pin
  now surfaces a clear warning, and the new helper is covered by tests for unsigned-from-pinned,
  blank-handle, crypto-absent, TOFU-pins-only-valid, never-pins-invalid, and pinned key-swap.
- **(MED) Concurrent-write TOCTOU.** Mitigated by the atomic replace; full file locking is
  disproportionate for a single-user local store (a lost pin re-establishes via TOFU) — documented.
- **(LOW) Dead `registered_sign_pub`** removed from `job_view` (D23 vestige, unused under D25); the
  `operator.py` module docstring now lists every command.
