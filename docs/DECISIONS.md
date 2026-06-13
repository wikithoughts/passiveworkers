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

## D26 — Input/output hardening: scrub the brief, the batch ingress, and the report
**Decision:** Close the remaining untrusted-data seams in the single-player engine, grounded in a
6-dimension audit of the engine against the founder's own deep-research report. Three in-scope hops
were unprotected:
- **The brief.** The one user-controlled input flowed RAW into every prompt (angle planning, query
  planning, drafting, the judge) and across the MCP boundary. New `sanitize.sanitize_brief()` strips
  invisible/bidi/HTML-comment vectors, collapses runaway whitespace/newlines, and HARD-CAPS length
  (`PW_MAX_BRIEF_CHARS`, default 4000 — an unbounded brief is a context-exhaustion vector across the
  whole multi-model pipeline). Applied at `local.run()` (raises on empty) and via a testable
  `mcp_server._normalize_research_args()` that also clamps depth/analysts/scope (clean error, never a
  traceback). The brief is the TASK, so it is sanitized+bounded but NOT spotlighted.
- **The batch ingress.** `batch.py` interpolated the per-item value RAW in the non-fetch path (the
  fetch path already spotlighted page text). Now the untrusted **item** is `spotlight()`-ed (data,
  never instructions) and the **instruction** is `clean()`-ed — closing a direct injection seam in
  the marketplace worker code.
- **The synthesized report.** Model output (merge/deliberate, the council read, the editor's
  summary/agreements/differences, and each per-analyst contribution) reached the final report
  without re-sanitization, so a model could re-emit hidden characters smuggled from an injected
  source. New `sanitize.strip_invisible()` removes those vectors **without** touching visible layout
  (markdown lists, code, `[S#]`/`[L#]` citations preserved) and is applied at every output→report hop.
**Why:** "Continue" → the founder chose deepening the published local engine (the adoption engine,
D16) over more marketplace work. The audit found the highest value-to-effort wins were NOT more SOTA
RAG (low leverage on a tool whose proven edge is currency, not recall) but cheap, high-confidence
hardening of the actual ingress/egress — the report's Information-Flow-Control principle applied to
the real trust boundaries. The report's entire computer-use/browser-agent half stays correctly out of
scope (no browser, ever — D16/D18).
**Status:** Settled & implemented. 102 tests green. No new dependency; no change to the
browser/session/tool-privilege boundaries.

### D26 addendum — adversarial review pass (input/output hardening)
A workflow review (2 lenses × adversarial verify, 12 agents) confirmed 6 findings — all one root
cause I missed: **the first cut hardened the LOCAL entry points (`local.py`, `mcp_server.py`) but not
the NETWORKED coordinator path.** The brief/instruction flowed from the coordinator's HTTP API →
`store.create_job` → `agent.py` → every `judge` method **raw**. Impact is bounded (models hold zero
tool privileges — at worst bad prose), but it was a real regression vs. D26's stated goal. Fixed:
- **Choke point at `store.create_job()`** — `question` and `context` are `sanitize_brief()`-ed there,
  so EVERY job type (chat/research_report/shard_map/assisted) and every downstream prompt + the report
  get a clean, bounded brief regardless of which endpoint or caller created the job.
- **Defense-in-depth at the agent** — `agent._do_answer` / `_do_judge` re-sanitize the question too
  (the coordinator is not fully trusted, cf. D25): a hostile coordinator can't slip a hidden payload
  into a worker/researcher/judge prompt even by crafting a payload directly.
- **Editor-prompt input hop** — `compile_report` now `strip_invisible()`-es each contribution before
  it enters the editor's prompt (not just on the way out), so a researcher model that re-emitted a
  smuggled char can't influence the editor.
- **Judge `reason` fields** — `score()` and `compare()` now strip their model-returned reason (the
  `compare` reason is printed to stdout in `run_demo`).
Regression tests added (5): `create_job` scrubs + length-bounds the question; the editor prompt
contains no hidden chars; `score`/`compare` reasons are stripped. Lesson: when hardening an ingress,
enumerate **every** entry point — the networked path is a separate trust boundary from the local CLI.

## D27 — Honest citation-fidelity eval: does each cited claim appear in its source?
**Decision:** Ship the first of R14's flagged eval instruments — a measurement of the product's
core promise (*grounded* research), not a trivia benchmark. New `council/fidelity.py` is a PURE,
dependency-free, lexical **grounding floor**: for each `[S#]`/`[L#]` claim it computes content-token
overlap with the cited source's text (reusing `retrieval.tokenize` so it tokenizes exactly like the
retriever it grades) and flags multi-digit numbers/years stated in a claim that are absent from its
source (the classic fabricated statistic). `scripts/eval_citation_fidelity.py` runs it two keyless
(no-API-cost) ways: **Mode A** scores a saved report by re-fetching its cited URLs; **Mode B** runs
the engine fresh and scores each analyst draft against the *exact extract the model read* (via the
new env-gated `PW_CAPTURE_EVIDENCE` capture in `researcher.py`) — reproducible, no network re-fetch,
no page-drift. Buckets: GROUNDED / WEAK / UNGROUNDED / UNVERIFIABLE / NO_CONTENT; the headline
"grounded rate" is *of verifiable claims*, with unreachable sources counted separately (never as
failures).
**Why:** The audit (D26) named citation fidelity the single most decision-guiding instrument: a
research tool's whole credibility rests on "when it says X [S3], does S3 say X?". This is the honest
counterpart to SimpleQA — it measures trustworthiness of the citations, the thing the council
architecture claims to protect. It is deliberately a *floor*: lexical overlap catches off-topic
citations and absent numbers (the common, damaging failures) but **cannot** prove semantic
faithfulness — so a GROUNDED verdict means "not obviously fabricated", never "verified true". Built
keyless and locally-runnable so it costs nothing to run repeatedly; the *currency-gap* instrument
(council vs BYOK frontier, which spends API credit) is the deliberately-separate next round (R16).
**Status:** Settled & implemented. 124 tests green (22 new). No new dependency. Evidence capture is
**local-only by construction** — suppressed whenever `PW_COORDINATOR` is set, so captured page text
can never reach a coordinator.

### D27 addendum — adversarial review pass (citation-fidelity eval)
A workflow review (4 lenses — security / correctness / honesty / integration — × adversarial verify,
18 agents) confirmed **10 of 14** findings; all fixed before commit:
- **(HIGH) Evidence-capture federation leak.** `PW_CAPTURE_EVIDENCE=1` attached ~1500-char page
  extracts to the returned `sources`, which a *federated worker* POSTs to the coordinator — leaking
  untrusted third-party page text off-machine. Fixed: capture is now suppressed whenever
  `PW_COORDINATOR` is set (the eval drives the worker in-process, where it is unset), so capture is
  local-only by construction.
- **(HIGH) Numeric format-drift false positives.** `[a-z0-9]+` tokenization split `4.2 million` →
  `4`,`2`, flagging a "missing number" against a source written `4,200,000`. Fixed with
  `significant_numbers()` — only **pure multi-digit** integers/years are treated as checkable facts
  (single digits, decimals, and alnum codes like `v1` are excluded as unmatchable by token overlap).
  This also resolved a second finding (alnum codes mis-flagged). A genuine fabricated stat (`42%`) is
  still caught; `4.2 million` vs `4,200,000` no longer false-flags.
- **(MED) Mode-A report path is attacker-influenceable data.** A saved report can list arbitrary
  URLs/paths. Hardened the re-fetch path: `_read_local` now refuses **symlinks** (no
  `/tmp/link→/etc/secret` follow) on top of its size cap; `score_report` caps unique URL re-fetches
  (`MAX_REPORT_URLS=200`, logged when hit) so a hostile report can't fan out thousands of GETs; a
  defensive results ceiling (logged, never silent) bounds memory.
- **(MED/LOW × 4) Honesty disclosures sharpened.** The output and docstrings now state plainly that
  Mode B measures faithfulness to the *~1500-char window the model saw* (not real-world accuracy);
  that Mode A suffers page-drift (false UNGROUNDED/UNVERIFIABLE); that union-over-cited-sources can
  hide cross-source conflation; and that the 0.5 GROUNDED threshold is an uncalibrated heuristic
  (`--grounded` to tune). The instrument must never read as more than the floor it is.
Regression tests added (5): federation suppression, format-drift/code number filtering, symlink
refusal, size-cap, and URL-cap. Lesson: an eval that *measures* honesty is held to the same bar — its
own caveats are part of the deliverable, and its untrusted-input path (a report file) is a real trust
boundary, not just test data.

## D28 — Currency-gap eval: where live-web research beats a frontier model's memory
**Decision:** Ship Track B instrument #2 — the measurement of the product's *claimed* edge. The audit
(D26) and our own trial showed the council's advantage is **currency, not raw capability** (a frontier
model wins on static knowledge; the council wins when the answer changed after the model's training
cutoff). `scripts/eval_currency_gap.py` makes that legible as an accuracy matrix by **currency-window
(static / recent / breaking) × category**: the local council (live web, FREE) vs a BYOK frontier model
(parametric, NO web, PAID via OpenRouter), each answer graded blind 0-10 against a curated **reference**.
The gap is a **paired** mean (council − frontier over questions both answered), with `static` as a
fairness control (currency irrelevant → expect ~0). Pure logic (validation / matrix / grade-parse /
summary-extract) is unit-tested; the council/frontier/grader I/O reuses existing plumbing
(`local.run`, `_ApiEditor`, `Judge`, `_extract_json`).
**Why:** It is the most honest possible self-assessment: it names *where the frontier wins* (static) and
only claims an edge where live grounding earns it. It completes the Track-B pair (D27 fidelity =
"are the citations real"; D28 currency = "is the freshness edge real"), the two instruments the audit
ranked most decision-guiding. Ground truth is treated as a **living, human-maintained input** — the
static control set ships ready; recent/breaking references are `VERIFY` placeholders the human fills
(my knowledge cutoff is pre-run-date, so fabricating post-cutoff "truth" would be dishonest; the
founder's research loop is exactly the right source).
**Why it spends money — and the guardrails:** this is the ONE instrument with a paid dependency (no
free frontier exists; that's the whole comparison). So it does **nothing paid by default** — bare
invocation is a `$0` dry run that validates references, estimates cost, and prints the exact command.
A paid run needs `--run` **and** `OPENROUTER_API_KEY` in the env (read from nowhere else), refuses if
all references are placeholders, and is capped at 40 questions unless `--max` is passed. The actual
paid run stays **founder-gated** — the script is complete and ready; the spend is his to authorize.
**Status:** Settled & implemented (dry run verified `$0`). 133 tests green (9 new). No new dependency.

### D28 addendum — adversarial review pass (currency-gap eval)
A workflow review (3 lenses — spend-safety / correctness / honesty — × adversarial verify, 27 agents)
returned 24 findings; **8 were verifications that the design is correct** (dry-run spends nothing; the
paired-gap math is honest; the summary regex, placeholder gate, and key-from-env-only all hold), and
**10 were actionable**, all fixed before commit:
- **(HIGH) Unbounded paid-run cost.** `--questions bigfile.json --run` with no `--max` would fan out
  one paid call per question. Added a 40-question ceiling that aborts a paid run pending an explicit
  `--max` (verified to abort *before any network call*).
- **(HIGH) Grader conflict-of-interest.** `--grader api` defaulted the judge to the *same* frontier
  model that wrote the baseline — it graded its own answer. Now a loud warning at run time + in the
  `--grader` help + the HOW-TO-READ notes; the free local grader remains the default.
- **(MED/LOW × 8) Honesty & clarity.** Surfaced the frontier model + cost in the dry run; added a
  `paired` column and a `⚠` flag on small samples (paired n < 3 = noise, not signal); retitled the
  matrix to emphasise the **WITH-web vs WITHOUT-web asymmetry** (it measures live grounding, not that
  the frontier is weak); reworded the argparse description so it can't be quoted as a general
  "council beats frontier"; disclosed that each reference is a single curated answer and that a stale
  reference silently corrupts the gap; made skipped-placeholder questions print prominently on `--run`.
Lesson: a *paid* instrument's spec is mostly its guardrails — the review's highest-value catches were
the cost foot-gun and the self-grading bias, neither a logic bug, both real ways to mislead or
overspend. NEXT (R17+, lower priority): the audit's performance/quality backlog (dynamic source
routing, Ollama keep-alive, dense-cosine rerank, merge anti-garble).

## D29 — Performance & quality backlog: source routing, warm models, citation-safe merge
**Decision:** Work the audit's performance/quality backlog now that the eval pair (D27/D28) can
measure it. Four changes, each small and reversible:
- **Dynamic source routing (activate dead code).** `research.search_structured` already supported
  `engine=academic` (arXiv) / `engine=encyclopedic` (Wikipedia), but `researcher.py` only ever called
  `web` — the other engines were unreachable. New pure `research.route_engines(query)` returns
  `['web', …]` — always the egress-localized web (the moat) **plus** arXiv when a query signals
  academic intent and/or Wikipedia when it signals definitional intent. `researcher._collect` now
  queries the routed set (extras shallower so they augment, never crowd out web) and dedups by URL.
  Env `PW_SOURCE_ROUTING=off` pins it to web only. arXiv/Wikipedia are central APIs (no geo-moat) and
  are sanitized + spotlighted exactly like web content.
- **Ollama keep-alive (kill reload stalls).** Every local model call now sends a top-level
  `keep_alive` (env `PW_OLLAMA_KEEP_ALIVE`, default `30m`) so a model stays warm across a worker's
  multi-round pipeline and across a session — removing the 5–30s reload stalls between calls. Default
  is deliberately longer than Ollama's implicit 5m for a research session; set `PW_OLLAMA_KEEP_ALIVE=0`
  (or `5m`) to unload sooner on a memory-constrained machine.
- **Citation-safe merge.** The synthesis prompts (`merge`, `deliberate`) now instruct the model to
  preserve `[S#]/[L#]` markers verbatim — AND, as a hard guarantee, `_drop_invented_markers` strips any
  marker in the merged output that wasn't in a source answer, so a merge can never *fabricate* a
  citation even if it ignores the instruction.
- **CI currency.** Bumped `checkout@v5` / `setup-python@v6` / `setup-node@v5` / Node 24 ahead of the
  2026-06-16 Node-20 action deprecation.
**Why:** These are the cheap, high-leverage wins the D26 audit flagged: routing unlocks better sources
for the queries that need them without weakening the egress moat; keep-alive removes pure dead latency;
the merge guard extends D27's citation-honesty guarantee to the synthesis path. **Deliberately NOT
done — dense-cosine rerank as the default over hybrid RRF:** the audit suggested it, but D20's own
`bench_rag` measurement chose hybrid as robustness insurance; flipping a measured default needs a
measured reason, not a suggestion. Left as-is.
**Status:** Settled & implemented. 149 tests green (16 new). No new dependency.

### D29 addendum — adversarial review pass (performance backlog)
A workflow review (2 lenses — correctness / integration-safety — × adversarial verify, 28 agents)
returned 26 findings; most were verifications the changes are correct (keep-alive is top-level not
nested; the deliberate JSON template is intact; routing keeps web first and dedups; env read at call
time), and **4 were actionable**, all fixed:
- **(HIGH) Two missed model-call sites.** The first cut added keep-alive to 7 sites but missed
  `batch.py._generate` (per-item batch loop — the worst case for reload stalls) and
  `net/baseline.py._via_ollama` (the demand-metric baseline, whose timing would be unfairly skewed by
  reloads vs the warm council). Both fixed + regression-tested. *Same lesson as R13/R14: enumerate
  EVERY call site — a grep for `api/generate` would have caught it; I trusted my mental list.*
- **(HIGH) Merge could still invent a citation.** A prompt rule can't bind a small model, so added the
  `_drop_invented_markers` hard guard described above.
- **(LOW) Encyclopedic over-routing + docs.** Anchored `who/what is` to query start (a mid-sentence
  "what is it like" no longer triggers Wikipedia); documented the `0`/`false` off-values and the
  central-API nature of arXiv/Wikipedia.
Net: 16 tests across the round (incl. the 2 missed-site regressions + the invented-marker guard).

## D30 — Freshness-biased research: turn live-web access into *precise current* answers
**Decision:** Act on the R16 currency-gap finding. That run showed the council had live web access but
didn't convert it into precise current answers — it dated the EU AI Act milestone to 2027 (not
2026-08-02) and an FOMC meeting to 2023. R18 biases the research pipeline toward recency:
- **`research.extract_date_hint(url, text)`** sniffs a publication date from a source (URL path or
  snippet); **`order_by_recency(evidence)`** sorts freshest-first (real fetched date > hint > sniff;
  undated keep relevance order, behind). `researcher.research()` date-hints all evidence and reorders
  **before the cap** (so fresh sources survive + get page-fetched first) and again after fetch.
- **Date-aware prompts:** the planner and drafter are told today's date; the drafter must "trust the
  MOST RECENT source, state the date of time-sensitive facts, and NOT rely on training-time memory for
  current dates." Each source is shown with its date.
**Why:** Currency is the product's one measured edge (D28). Live retrieval is necessary but not
sufficient — the model needs the freshest evidence *first* and an explicit instruction to prefer it.
**Verification (free, quick-depth, no API spend — re-ran the 4 failed questions on the fixed code):**
**3 of 4 fixed**, including the headline year-error — Python ✅ ("3.14.6, released 10 June 2026"),
EU AI Act ✅ ("August 2, 2026", was 2027), iPhone ✅ (iPhone 17e, March 2026, was wrong). **FOMC still
fails** ("June 2023") — and honestly so: the *query* "most recent completed FOMC meeting" returns the
SEO-dominant June-2023 meeting, and recency *ranking cannot rescue what search did not return*. That is
a retrieval problem (force the current year into time-sensitive queries / freshness-filtered search),
the clear R19 target — not a ranking one.
**Status:** Settled & implemented. 162 tests green. No new dependency. **Deliberately deferred to R19**
(Phase 2): auto-deepen depth for breaking-window queries, and code-level current-year query injection.

### D30 addendum — adversarial review pass (freshness)
A review (3 lenses — correctness / integration-regression / methodology — × adversarial verify,
17 agents) confirmed the design works and found **3 actionable bugs**, all fixed before commit:
- **(HIGH) Impossible dates.** The month-name regexes accepted day `0`/`32`, emitting strings like
  `2026-02-30` that corrupt the lexicographic sort. Fixed: day pattern restricted to 1-31 **and**
  every full date is validated with `datetime.date()` (impossible dates fall back to month-year/empty).
- **(HIGH) Topic-year false positives.** A bare year in prose ("the 2008 crisis" in a 2026 article)
  was sniffed as the publish date → a fresh source wrongly ranked old. Fixed: bare years are trusted
  **only from URL paths**; from free text only *full, validated* dates count.
- **(HIGH) Over-recency on stable facts.** Recency reordering could bury an authoritative older source
  under a recent repost on static questions. Fixed: reordering is gated on `is_time_sensitive(brief)`
  (recency keywords) so stable-fact briefs keep relevance order — protecting the eval's static control.
Lesson: a ranking heuristic's failure modes are mostly bad *inputs* (malformed/ambiguous dates) and
mis-*scoping* (applying it where recency is irrelevant) — the review caught both.

## D31 — Current-year query injection + breaking auto-deepen: fix what RANKING can't
**Decision:** Close the FOMC residual D30 surfaced. R18 reorders evidence by date, but *you cannot
reorder a fresh source that search never returned* — and the FOMC query ("most recent completed FOMC
meeting") returns the SEO-dominant June-2023 page, so recency ranking has nothing current to lift.
That is a **retrieval** problem; R19 fixes it at the query, deterministically (not relying on the small
planner model to comply with a "use the current year" instruction):
- **`research.inject_recency(query, today, time_sensitive)`** pins the current year into a
  time-sensitive **web** query so the engine surfaces *this year's* results, which R18 then orders
  freshest-first. Web only — arXiv (relevance) and Wikipedia (full-text) don't SEO-stale, and a bare
  year pollutes them. Wired into `researcher._collect` for the `web` engine on both plan and refine
  queries; gated on the brief-level `fresh` flag.
- **`research.is_breaking(text)` + `researcher._bumped_depth(brief)`** give a genuinely *breaking*
  brief one extra depth notch (more refine rounds + a bigger cap + more page fetches) to outrun
  SEO-stale pages. `is_breaking` is a strict subset of `is_time_sensitive` — plain "latest"/"current"
  are handled by the *cheap* year injection, so we don't double the local compute on every dated query.
**Why:** Currency is the product's one measured edge (D28). Live retrieval + freshest-first ranking
(D30) are necessary but not sufficient when search itself returns only stale pages — the fix has to
act *before* ranking, on what the engine is asked.
**Status:** Settled & implemented. 184 tests green (22 new). No new dependency.

### D31 addendum — adversarial review pass (currency injection)
A workflow review (3 lenses — correctness / retrieval-efficacy / regression-consistency — × adversarial
verify, 18 agents) returned 15 findings, **9 confirmed** (no critical). Four real defects, all fixed
before commit; two MEDIUMs accepted as documented trade-offs:
- **(HIGH) "Already pinned" over-detected.** The first cut skipped injection whenever `\b20\d{2}\b`
  matched — so a *price* (`$2000`), a *count* (`2048`), or any non-year 20NN **silently suppressed**
  the fix on exactly the concrete current-fact queries it targets. Fixed: only a *standalone, plausible*
  year (1990‥current+1, not currency/word-fused) counts as pinned.
- **(HIGH) Stale planner year recurred.** The same weak model that omits the year also *hallucinates*
  a stale one ("rate **2023**"); the old guard no-op'd → the 2023 page returned again, reproducing the
  exact bug R19 exists to kill. Fixed: a *recently*-stale year (within `_STALE_WINDOW=4`) gets the
  current year **appended alongside** it (never string-*replaced* — so a deliberately historical query
  is never corrupted), and the engine sees the fresh signal for R18 to lift. A deep-historical year
  (e.g. "the 2008 crisis") is respected.
- **(HIGH) Historical sub-queries poisoned.** `fresh` is brief-level, so on a mixed-intent brief
  ("history *and current state* of X") a historical sub-/refine-query ("…1970s") got "2026" appended.
  Fixed: a per-query historical/timeless suppressor (`_HISTORICAL_RE`) skips injection on history/
  definition/decade queries — deliberately **excluding** "what is/are" (too common in legitimate
  current questions, incl. the FOMC brief itself).
- **(HIGH) Stable briefs over-deepened.** `is_breaking` fires on "developing **story**", "**live
  updates** plugin", "this **morning** yoga" — none time-sensitive — so a `quick` caller was silently
  pushed into an extra refine round. Fixed: the depth bump is gated on `is_time_sensitive(brief) AND
  is_breaking(brief)`, restoring the intended breaking ⊂ time-sensitive invariant.
- **(also fixed, robustness)** `_year_of` switched from an anchored `re.match` to `.search`, so a
  non-ISO `today` ("June 13, 2026") still yields the year instead of silently disabling the lever.
- **Accepted trade-offs (documented, not fixed):** a year *fused* to a word (`fy2025`) can still get a
  second year appended (rare; the bare intent is preserved, and fixing it conflicts with the higher-sev
  price case); and a literal year is a soft-AND term, so an evergreen page that omits the year string
  can rank lower — the page-fetch + `order_by_recency` + the 12-16 evidence cap keep it in play and
  re-rank it by real date.
Lesson: a query-rewrite heuristic's failure modes are *over-trusting a token's meaning* (is "2000" a
year or a price?) and *brief-vs-query scoping* (a fresh brief still has historical sub-queries) — and
the safe move on ambiguity is to **append, never replace**, so a wrong guess never corrupts the query.

## D32 — Distributed task orchestration: "make your computer work for you AND others" is the product
**Decision:** Reframe and build toward the founder's full vision. Passive Workers is **not** "a local
research tool" — it is *make your computer work for you and others*: a local-first network where
machines do **typed jobs** for each other. Deep research is the **flagship single-player task** (the
adoption engine, D16) — one task type among many, not the product. The vision: send a job → the
network **splits it across available computers** (auto by capacity or a user-specified split) → each
does its chunk **locally** → the parts are **reassembled and delivered back**, with **progress %**,
**load-balancing**, and **failover** when a node stalls.
A 3-agent review found the vision is blocked by **orchestration gaps, not constraints** — the legal/
crypto/trust groundwork is already settled and built (D4 owned-deliverables; D15 sharded-batch
envelope; D18 human-mediated computer-use; escrow + score-weighted conserved settlement; content-
addressed + signed delivery; SSRF guards; the `JOB_TYPES` registry). So R20 builds the **scheduling
half** on the existing `shard_map` machinery (Phase-1):
- **Failover** (`store._reap_once` + `_pick_replacement`/`_reassign_task`): a not-done task whose node
  went OFFLINE or whose claim is held past a claim-timeout is *reassigned* to a fresh capable node
  (re-queue, new owner, claim cleared, `retries`+1) instead of failing the whole job. The job fails
  only when no replacement exists or `PW_MAX_TASK_RETRIES` is hit. Reassignment is **pre-settle and
  never touches the ledger** → settlement still runs once at the end over whoever actually completed,
  so **conservation is preserved** (D1/D2 — no node-to-node transfer).
- **Progress** (`update_task_progress` + `POST /tasks/{id}/progress` + `job_view.progress`): a worker
  reports `done/total` mid-flight (node-ownership enforced); `job_view` exposes a job completion
  fraction. `BatchWorker` emits it per item (throttled by the agent).
- **Capacity-weighted + user split** (`create_job` + `_capacity`/`_apportion`): shard sizes are
  weighted by node cores/RAM/load, or by an explicit `split`, replacing flat round-robin; the global
  item index is preserved so in-order reassembly is unchanged. Never more workers than items.
**Honoring designs for the new task types (steel-manned, inside the envelope; Phase-2):**
- **"Tasks needing downloading"** → `shard_map fetch:true` *compute-over-fetched-data*: the node
  fetches a PUBLIC URL it could lawfully fetch alone and returns the model's **extraction**, never the
  raw bytes (D4; preserve `batch.py`'s "return output, never content"). Requires SSRF redirect/TOCTOU
  hardening first.
- **"Coding parts then connecting them"** → code **generation** distributes as `shard_map` (each node
  returns generated code = an owned artifact); code **execution/integration** routes through the
  human-mediated `assisted` path (D18) — running third-party code on contributor machines stays the
  parked/gated track (D15).
- **Verification of chunks**: fuzzy QA (`judge.spot_check`) for generative chunks; content-address +
  signature for file chunks (D5/D10/D22/D23).
**Why:** The network is the reason for the name; the strategy docs (D13 typed-job marketplace, D16
single-player adoption engine, D21 assisted centerpiece) already define it — the gap was surfacing it
in the copy and building the scheduler. Single-player polish stays the lead hook (it brings the
operators), so the reframe elevates the network without demoting research.
**Status:** Settled & Phase-1 implemented. 210 tests green (20 new: failover, shard orchestration,
federation HTTP). No new dependency. **Phases 2-3 are roadmap** (task-type dispatch registry +
download-extract/code-generation types; multi-producer file reassembly; pipeline/DAG by generalizing
`_maybe_start_judging`; security hardening — rate limiting, SSRF redirect/TOCTOU pinning, per-operator
enrollment tokens).

### D32 addendum — adversarial review pass (orchestration Phase-1)
A workflow review (3 lenses — correctness / conservation / security — × adversarial verify, 8 agents)
returned 5 findings, **4 confirmed**, all fixed before commit:
- **(CRITICAL) Non-finite split weights crashed the `/jobs` endpoint.** `split=[inf, 1.0]` passed the
  naive `w > 0` check (inf > 0 is True) but made `_apportion` compute `inf/inf = nan` → `int(nan)`
  raised `ValueError`. Fixed: the split guard requires `math.isfinite(w)`; an invalid split silently
  falls back to capacity weighting rather than erroring the job.
- **(HIGH) Progress didn't reset the claim clock → a slow-but-honest node was reassigned out from
  under itself** (duplicate work). Fixed: a *forward* progress report (`done` strictly increases)
  resets `claimed_at`, so a node that is visibly working keeps its task.
- **(MED) Progress had no spam guard.** Fixed in the same change: a *non-advancing* report is ignored
  (no write, no claim reset) — so a node can neither spam writes for lock contention nor keep a
  stalled claim alive without actually progressing. (Full server-side rate limiting is Phase-3.)
- **(LOW) `done > total` was silently clamped** (misleading record). Fixed: it's now rejected — a
  bounded, ordered contract.
Lesson: the dangerous inputs in a scheduler are the ones that *look valid* — `inf` passes `> 0`, and a
"progress" signal can be weaponized both ways (to dodge failover, or to spam) unless it must represent
*real forward motion*. The conservation invariant held throughout (reassignment is pre-settle).

## D33 — Task-type dispatch registry + the download-extract & code-generation types (D32 Phase-2)
**Decision:** Make "research is just one task type" structurally true, and add the two task types the
founder named. A single **`TASK_BEHAVIORS` registry** (`council/net/config.py`) is now the source of
truth for how each job type is orchestrated — `TaskBehavior(executor, sharded, fetch, judge, assemble,
framing)` — replacing the ~5 scattered `if job_type == "shard_map"/"research_report"` conditionals
(coordinator split/assemble/judge-sample in `store.py`; worker executor/judge dispatch in `agent.py`;
the baseline-skip in `coordinator_app.py`). The refactor is **behavior-preserving** for chat /
research_report / shard_map; unknown/None → chat (never accidental sharding); `assisted` stays its own
human-mediated early-return lifecycle.
Two new types, both reusing the (D32-hardened) shard scatter/gather:
- **`download_extract`** — sharded, **forces `fetch=True`** (items are PUBLIC URLs). Each node
  `fetch_extract`s and returns the model's **extraction**, never raw bytes — *compute-over-fetched-
  data* (D4: the node fetches a page it could lawfully fetch alone; it is NOT a proxy). Inherits the
  existing SSRF host guard (redirect/TOCTOU hardening is Phase-3).
- **`code_generation`** — sharded; each node generates ONE self-contained code unit per spec. A
  trusted per-type `framing` ("output only code; do NOT run or install anything") is prepended to the
  sanitized instruction at the worker. **Generation only** — running/linking the code is the gated
  track (D15/D18) and routes through `assisted`; nothing here ever executes generated code.
Both settle via the existing in-order shard assembly (the judge `spot_check`s quality), so ledger
conservation and input-order reassembly are unchanged.
**Why:** D13/D16/D21 always framed the product as a typed-job marketplace with research as the
flagship; the registry removes the architectural debt that made adding a type a 5-site edit, and the
two types realize the founder's "tasks needing downloading" and "coding parts" inside the settled
legal envelope (D4/D15/D18) with zero new exposure.
**Status:** Settled & implemented. 217 tests green (7 new). No new dependency. **Still roadmap (Phase-3):**
multi-producer file reassembly; a 2-stage pipeline/DAG (generalize `_maybe_start_judging`) so a
code_generation batch can hand to an `assisted` integrate/build step; security hardening (rate limiting,
SSRF redirect/TOCTOU pinning, per-operator enrollment tokens).

### D33 addendum — adversarial review pass (registry + new types)
A workflow review (3 lenses — regression / constraints / security — × adversarial verify, 6 agents)
returned 3 findings, **2 confirmed (same root cause), fixed before commit:**
- **(HIGH) A user-supplied `fetch=True` could turn `code_generation` (or any non-download sharded
  type) into a fetcher.** My first cut wrote `payload["fetch"] = beh.fetch or fetch`, so the asker's
  flag could make a code-spec batch try to `fetch_extract` each "spec" as a URL — semantic confusion
  and a D15 violation (code_generation must never fetch). Fixed: fetch is now **type-driven, not
  user-overridable** — added `allow_user_fetch` to the registry; `download_extract` always fetches,
  `shard_map` honors the asker's opt-in (its original D15 behavior), `code_generation` NEVER fetches
  regardless of input. Regression-tested across all three types × the flag.
Lesson: a per-type capability flag must not be silently widened by a shared user input — "who decides
whether this fetches?" is the type's contract, not the caller's. The registry made the right answer a
one-line per-type declaration.
