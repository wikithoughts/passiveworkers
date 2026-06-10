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
