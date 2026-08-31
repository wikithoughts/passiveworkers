# Passive Workers — Economics

> How the credit works. See [DECISIONS.md](DECISIONS.md) D1/D6 for the non-negotiables and
> `passiveworkers/ledger.py` for the implementation.

## The credit

- **Non-transferable.** There is no account-to-account transfer. Credit moves **only** through job
  settlement (payment for real work). This is enforced in code: the ledger has no `transfer()`.
- **Denominated in normalized compute units** (work, not dollars) — gives value to compute and
  sidesteps the fact that there's no canonical real-time price of compute.
- **Money only at the edges** (later): top-up to inject credit, payout to cash out. Never a tradeable
  instrument between users.

## The give/take rule (mutual aid, enforced)

Every account opens with a **starter allowance** so a newcomer can ask before contributing. To keep
asking once it's spent, you must **earn by helping**. A pure free-rider depletes to zero and is
**blocked** — "no one only-takes, no one only-helps," enforced by balance, not trust.

> Demonstrated in `run_demo.py`: `leo` (only asks) is blocked on his 3rd request; `alice` (helps and
> asks) stays net-positive because her machine helping others earns credit back.

## Ideas compete (score-weighted payouts)

A job's **worker pool** is split among helpers **in proportion to the judge's quality score**, so a
better answer earns more credit than a worse one for the same question. The judge takes a fixed fee.

## Conservation (no minting bugs)

Every job is **conserved**: the asker's debit equals exactly the sum credited to helpers + judge. The
split allocates the **remainder to the last payee** so rounding can never mint or burn fractional
credit. Property-tested across 3,000 randomized scenarios with zero drift.

Current parameters: `STARTER_ALLOWANCE = 100` (`PW_STARTER_CREDITS` in `passiveworkers/ledger.py`) applies to
both paths below. Pricing itself has two separate sources of truth, and only one is live:

- **Legacy in-process demo** (`passiveworkers/coordinator.py`, driven by `passiveworkers/run_demo.py`): fixed
  `WORKER_POOL = 30`/job, `JUDGE_FEE = 5`/job — no per-job-type scaling.
- **Live network** (`passiveworkers/net/`, D50): priced by `pool_for(job_type, n_minds)` in
  `passiveworkers/net/config.py`, driven by the `PW_WORKER_POOL`/`PW_JUDGE_FEE` env vars (same 30/5 defaults) —
  but the per-mind price scales by the number of responding nodes and by each job type's `pool_mult`
  weight (e.g. `chat` is 1×, `research_report` is 3×, `assisted` is 5×), so the real price is not a flat
  constant. `GET /job-types` on any live coordinator serves the exact current numbers (including
  `judge_fee`) for that deployment — the one place to read actual pricing rather than this doc.

## Sustainability & contributor pay (later)

- A transparent, earmarked **maintenance fee** (not silent decay) funds operations.
- When cash enters, contributors are paid **cost-plus over marginal electricity** — never pegged to
  the fast-falling token price index (which would push them below break-even and cause churn).

## Legal posture (summary; see DECISIONS D1)

Non-transferable + earned-only cashout is designed to avoid **both** securities law (no appreciation,
no secondary market) **and** money-transmitter status (closed-loop, seller-of-services). Cash payouts
later: 1099-NEC at $2,000 (2026); route via a TPSO to push KYC/AML to the processor.
