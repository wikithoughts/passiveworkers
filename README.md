# Passive Workers

**An open marketplace where computers do work for other computers** — "Upwork for computers."
You submit a brief; machines around the world work on it for minutes (not seconds); a judged,
cited deliverable comes back. Contributors earn a **non-transferable credit** their own
machines spend on jobs. Open-source, self-hostable, mutual-aid economics.

**The flagship job type — Distributed Deep Research:** the only deep research performed by
many real computers in many real countries. Every node researches the live web *from its own
country's vantage* (its own egress, its own local sources — never proxied traffic), cites its
sources, and a blind editor compiles one report: executive summary, where the countries agree
and differ, findings by country. Centralized deep research is one mind behind one datacenter
egress; this is N independent minds with genuinely different windows on the web.

We do not compete on instant answers or cheap inference (measured: a frontier model wins that
0/10 — see `docs/TRIAL_RESULTS.md`). We compete in a different latency class, on what a
centralized model structurally cannot have: **in-country sources, live currency, plural
perspectives, privacy, and a commons.**

> Start small (two computers) → grow the network → commercialize to companies later.
> Mission-first **commons**; money only ever at the platform edges; **no token, no secondary market.**

## Documentation

Start with **[`docs/CONTEXT.md`](docs/CONTEXT.md)** (what we're building and why) and
**[`docs/ROADMAP.md`](docs/ROADMAP.md)** (what's next). Full set:

| Doc | What |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | The why, the history, the layered vision (read first). |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones M0–M5 with status (living tracker). |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Roles, current vs networked shape, portability, trust/security. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Settled decisions + rationale (ADR-style, append-only). |
| [docs/ECONOMICS.md](docs/ECONOMICS.md) | Credit, give/take, score-weighted payouts, legal posture. |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Terms (Council, worker, judge, lens, credit…). |

## The Council MVP (`council/`)

```
  Asker ──question──▶ Coordinator ──fan-out──▶ diverse workers ──answers──▶ Judge
                          │                                                    │ score (blind) + MERGE
                          ◀──── merged answer + who-contributed-what ◀─────────┘
                       Ledger: debit asker, credit helpers + judge (give/take stays balanced)
```

| File | Role |
|---|---|
| `council/worker.py` | A "perspective" agent: one Ollama model + a lens + a country tag. Returns an **owned answer**, never proxied traffic. |
| `council/judge.py` | Scores candidates **blind** (ideas compete), then **merges** them into a diversity-preserving synthesis. Plus a blind A/B verifier. |
| `council/ledger.py` | Non-transferable credit with **give/take enforcement**, score-weighted payouts, per-job conservation. |
| `council/coordinator.py` | Concurrent fan-out → judge → score-weighted settlement. |
| `council/run_demo.py` | End-to-end runnable demo + verification. |

## Run it

```bash
# 1) Local model server
ollama serve            # then pull a few diverse models, e.g.:
ollama pull gemma3:4b && ollama pull gemma2:9b && ollama pull gemma3:12b
ollama pull qwen2.5:14b && ollama pull mistral-small:22b

# 2) Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3) The whole idea, end to end
python -m council.run_demo
```

The demo proves: the **merged** answer beats the best single model (blind A/B by an independent
verifier), diversity is captured, credit is **conserved**, and a **free-rider is blocked**.

## What makes the moat real

Model diversity alone is buyable centrally (fan out to 5 models from one laptop). The part that
genuinely needs a network of real people's machines is narrower and specific:
**authentic in-country web presence, privacy/sovereignty, the open-source commons, and the
mutual-aid credit economy.** The MVP proves the loop with model-diversity today; the
**geo-diversity** moat activates when a second machine in a different country joins the coordinator.

## Guardrails (settled — see the plan/memory)

- Never pitch "cheaper inference." Compete on diversity / quality / privacy / sovereignty / commons.
- Nodes return **owned deliverables**, never tunnel others' traffic (keeps us clear of the
  residential-proxy / exit-node legal trap).
- Non-transferable credit, no token, no secondary market; money only at the edges.

## Networked (Mac ↔ VPS)

The coordinator (`council/net/`) runs on a remote host (reusing its Ollama, loopback-only); the Mac
joins over an SSH tunnel. Deploy/run/demo with `scripts/deploy_vps.sh`, `vps_run.sh`,
`cross_country_demo.sh`. A **live operator map** is served at `GET /dashboard` (nodes by country, load,
reputation, recent jobs). See [docs/ROADMAP.md](docs/ROADMAP.md) for status.

## Status

- ✅ Council MVP (local) + **networked Council across two countries** (Mac + Helsinki VPS), judged merge, conserved give/take ledger.
- ✅ Reputation/quality tracking + live `/dashboard` map.
- ✅ Cross-hardware verification recorded → verification is **quality/judge-based**, not model-identity (see [docs/DECISIONS.md](docs/DECISIONS.md) D10).
- ✅ Spike 2 (worker/Ollama). Spike 1 superseded by the real cross-hardware finding.
