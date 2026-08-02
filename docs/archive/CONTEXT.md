> **Archived 2026-08 — superseded by [../VISION.md](../VISION.md). Kept for history.**

# Passive Workers — Context

> The single place a new collaborator (human or AI) reads first to understand **what we
> are building, why, and how we got here.** Pairs with [ROADMAP.md](../ROADMAP.md) (what's next)
> and [DECISIONS.md](../DECISIONS.md) (what's settled and why).

## What Passive Workers is

A network of contributed computers + a **non-transferable credit** + an open-source
coordinator, producing **varied intelligence, not cheap intelligence.**

The first product — **"The Council"** — is an open-source, peer **mutual-aid AI-collaboration
network**: you help others with AI tasks, others help you, and a non-transferable credit keeps
the give-and-take balanced. A task is run across a **diverse council of nodes** — different
models, different reasoning lenses, and (the real moat) **different countries' web access** —
then **judges score the answers and merge them** into a result better than any single node's.

## North star

Democratize AI and **break the intelligence concentration** held by a handful of companies —
give people and institutions a **global council of diverse perspectives** that no single
centralized model can offer. Not a cheaper API; a *different kind* of intelligence.

## Why this shape (the honest reasoning)

This direction is the survivor of a deliberately adversarial validation process. The findings
that shaped it (full detail in [DECISIONS.md](../DECISIONS.md)):

- **"Cheaper inference" is a dead end.** Centralized small-model inference is already
  effectively free (~$0.02/Mtok, free tiers) and 10–50× faster than a laptop. A consumer-compute
  network cannot win on price, so we don't try — we compete on **diversity, quality, privacy,
  sovereignty, and the commons**.
- **The token everyone else used to bootstrap is banned here on purpose.** Comparable networks
  (Bittensor, Kuzco, Grass…) manufactured "traction" with tradeable-token speculation; strip the
  token and the demand is near-zero. Petals — a *working* Llama-70B swarm — died for lack of a
  payment/demand layer. We refuse the token (it invites speculation, securities risk, and fake
  demand), which forces us to find **real** value.
- **The real value is varied intelligence + a mutual-aid commons.** Ensemble-of-diverse-agents
  with judge-and-merge genuinely beats single models; authentic in-country web presence is
  something centralized APIs structurally cannot replicate; and the dual-role give/take loop
  (contributors *are* the consumers) is the most realistic way to cold-start without cash or a token.

## The layered model (so the pieces don't conflict)

| Layer | What it is |
|---|---|
| **Substrate** | Contributed nodes + non-transferable credit ledger + open-source coordinator. |
| **First product — The Council** | Mutual-aid collective intelligence (varied answers via judge-merge). **The primary product and north star.** |
| **Later markets** | Research-compute commons (batch/science for under-funded labs); companies running private multi-node intelligence; a broad individual consumer layer. |

## How we got here (history, briefly)

1. Started from a broad "any computer earns AI credits for cheap personal inference" vision.
2. Adversarial research (14-agent validation + independent review) killed the "cheaper inference"
   premise and surfaced that demand — not technology — is the risk.
3. The founder reframed the start as an **open-source mutual-aid Council** (varied intelligence,
   two computers, recirculating credit, commercialize to companies later). That is the current
   direction; the research-compute commons and consumer layer became *later markets*.
4. Built and verified the **Council MVP** locally (see ROADMAP M1).
5. Now extending it to **two real machines** (this Mac + a VPS, ideally different countries) so the
   geo-diversity moat becomes real.

## Hard guardrails (never cross)

- Non-transferable credit, **no token**, no secondary market; money only at the platform edges.
- **Never** pitch "cheaper inference."
- Nodes return **owned deliverables**, never proxy/tunnel others' traffic.
- No blockchain as system of record; no full node per machine; phones excluded.

See [DECISIONS.md](../DECISIONS.md) for the full rationale behind each.
