# Passive Workers — Vision

> The single source of truth for what this product is, in what order, and why. If
> README.md, docs/CONTEXT.md, pyproject.toml, or ANNOUNCE.md ever say something that
> contradicts this file, this file is right and they need fixing — see the
> positioning-consistency check in docs/RELEASING.md.

Humanity's answers shouldn't come from one model in one company's datacenter.

## The monoculture problem

A handful of frontier labs now sit between most people and most AI-mediated answers.
That concentration has three costs, and none of them are hypothetical:

- **Currency.** A frontier chatbot answers from training data that is months or years
  old. When the honest answer is "as of when?", a frozen model is structurally wrong,
  not just occasionally wrong.
- **Consensus.** One model family makes one family of mistakes. Ask it five times and
  you get five confident variations on the same blind spot — not five independent
  checks on each other.
- **Sovereignty.** Every question routed through someone else's server is a question
  that server now has a copy of. For a lawyer, a journalist, a clinician, or anyone in a
  jurisdiction with real surveillance risk, that's not a convenience trade-off — it's
  disqualifying.

Meanwhile, most of the world's compute sits idle most of the time, on machines their
owners already paid for.

## The bet

We don't compete on being a bigger model. We compete on four things a bigger model,
by itself, structurally cannot offer:

| Pillar | Claim | Evidence that already exists |
|---|---|---|
| **Plurality** | Different model families make different mistakes; dissent is preserved, not flattened into a false consensus. | The agree / differ / unique sections in every report (`passiveworkers/local.py`, `passiveworkers/judge.py`); the blind judge that scores before it merges. |
| **Currency** | Live web beats frozen training data whenever "as of when?" matters. | `scripts/eval_currency_gap.py`; `docs/TRIAL_RESULTS.md` — the only trial wins were currency wins. |
| **Sovereignty** | Your models, your disk; nothing leaves but the search terms. | `passiveworkers/sanitize.py`; the SSRF-guarded fetch path; the "what leaves your machine" table in [SECURITY.md](../SECURITY.md). |
| **Commons** | Idle machines doing mutual aid — no token, no proxying, consent always. | `docs/DECISIONS.md` D1 (no token), D4 (no proxied traffic), D18 (informed tiered consent); `passiveworkers/ledger.py`'s conservation guarantees. |

## The layered model

| Layer | What it is | Status |
|---|---|---|
| **The engine** (single-player) | `pworkers research`: multiple local models research the live web as independent analysts; a blind editor writes one cited report. Judge the project on this. | Shipped, stable, the adoption path. |
| **The network** (opt-in) | The same repo's second half: machines doing typed jobs for each other through a coordinator, settled in non-transferable credit. Deep research is the first job type; batch, extraction, and human-in-the-loop `assisted` work exist too. | Shipped and live (a real multi-country deployment exists); invite-only while it hardens; self-hostable today via `docs/network/SELF_HOST.md`. |
| **Later markets** (speculative) | A research-compute commons for under-funded labs; companies running private multi-node intelligence; a broad consumer layer. | Not built. Named honestly as a direction, not a roadmap item. |

The engine is the flagship because it's the part a stranger can verify alone, on their
own machine, in the first five minutes, with nothing to trust but their own eyes. The
network is real, not a toy, but it asks for more trust up front (a coordinator, an
invite), so it earns that trust second.

## What we refuse

The most inspiring thing this project can say isn't a feature list — it's the list of
things we will not build, each one already load-bearing in the code, not just a
promise on a page:

- **No token, ever.** Credit is internal and non-transferable; money only enters or
  leaves at the platform edge. (D1)
- **No blockchain as system of record.** A single trusted coordinator plus a
  tamper-evident log solves this network's actual problem; a full node per machine
  would eat the idle resources we're trying to share. (D2)
- **We will not pitch "cheaper inference."** Centralized inference is already fast and
  nearly free; competing on price is a losing bet we refuse to make. We compete on
  diversity, currency, privacy, and the commons instead. (D3)
- **Nodes never proxy or tunnel someone else's traffic.** A machine in this network
  returns work it produced — never routed packets, never an exit node. This is the
  single most important legal and ethical line in the whole project. (D4)
- **No browser automation, no computer-use, no sessions, no cookies — ever.** Models
  return text; every action a computer takes is plain Python under this repo's
  control. When a task genuinely needs a real computer driven, it is handed to a human
  who consents to that one task and does it with their own AI or by hand. (D18)
- **No hidden work.** An operator always knows the *class* of work they opted into
  when they joined; the only thing forbidden is deception about what a task actually
  is. (D18, D53)
- **No secondary market, no speculation on the credit.** (D1, `docs/ECONOMICS.md`)
- **No arbitrary remote code execution, no full node per machine, no phone workers.**
  The network runs declared, structured task types only.

## How to help, by audience

- **Users:** run `pworkers research`, tell us where it breaks. A precise bug report against
  young, honestly-labeled software is a real contribution, not a complaint.
- **Operators:** join an existing cell ([docs/CONTRIBUTE_COMPUTE.md](CONTRIBUTE_COMPUTE.md))
  or start your own ([docs/network/SELF_HOST.md](network/SELF_HOST.md)) — you don't
  need an invitation to have a real, two-node commons.
- **Askers:** submit a job once the network exists for you to reach —
  [docs/network/ASKING.md](network/ASKING.md).
- **Contributors:** pick an item from [docs/ROADMAP.md](ROADMAP.md). Read
  [docs/DECISIONS.md](DECISIONS.md) first — most things that sound like a good idea to
  add were already considered and either shipped or explicitly refused, with the
  reasoning written down. Don't relitigate D1, D4, or D18 without a founder decision.
- **Skeptics:** run the evals yourself — `scripts/eval_currency_gap.py`,
  `scripts/eval_citation_fidelity.py`, `scripts/bench_simpleqa.py` — and read
  [docs/TRIAL_RESULTS.md](TRIAL_RESULTS.md), including where we lost.

---

We don't claim to be the biggest project for humanity to benefit from decentralized AI.
We claim to be honestly built, and we intend to earn the rest.
