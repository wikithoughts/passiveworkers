# Passive Workers — Architecture

> How the system is built. Pairs with [DECISIONS.md](DECISIONS.md) (why) and the code
> in `passiveworkers/`. Terms in **bold** are defined in [GLOSSARY.md](GLOSSARY.md).

## Two engines, one repo

`passiveworkers/` holds two things that share code but run independently:

1. **The research desk** (`pworkers research` / `pworkers serve`) — one process on your machine,
   local **analysts** + a **blind judge** + an **editor**, producing a cited report.
   This is the flagship, single-player, and works standalone with no network at all.
2. **The network** (`passiveworkers/net/`) — an opt-in commons where machines run the same
   underlying work as jobs for each other, coordinated by a **coordinator**, settled in
   non-transferable **credit**. Invite-only while it matures.

## Network roles

- **Asker** — submits a job and spends credit (`pworkers ask`).
- **Operator** / **node** — a contributor's machine, running one model with a **lens**
  and a **country** tag; returns an **owned deliverable** (never proxied traffic).
- **Judge** (network sense) — scores candidate answers **blind** and **merges** them
  into a diversity-preserving synthesis.
- **Coordinator** — the open-source, self-hostable hub: holds the **ledger**, the job
  queue, the node registry, and telemetry. Routes *jobs* and settles *credit*; never a
  token, never traffic.

Full definitions: [GLOSSARY.md](GLOSSARY.md).

## The legacy demo/eval path (M1 — local, in-process)

```
run_demo → Council(coordinator) ──fan-out──▶ PerspectiveWorker × N  (local Ollama models)
                                  ──score+merge──▶ Judge
                                  ──settle──▶ Ledger (in-memory)
```
Not dead — `passiveworkers/run_demo.py` and `scripts/merge_eval.py` still import this path and
are the quickest way to eyeball merge quality on a laptop with no coordinator running.
But it is not what `pworkers research`, `pworkers join`, or any documented user flow uses today —
those run the current shape below. Files: `passiveworkers/coordinator.py`, `worker.py`,
`judge.py`, `ledger.py`, `run_demo.py`.

## The current live shape (M2 — networked, dial-out only)

This is what `pworkers join`/`pworkers ask` actually run against today, not a future target:

```
        ┌─────────────── Coordinator (any host) ──────────────────┐
        │  passiveworkers/net/coordinator_app.py (FastAPI)               │
        │   • ledger + job queue + node registry + telemetry      │
        │   • SQLite (→ Postgres later), config via env           │
        │   • token-auth node registration, TLS via reverse proxy │
        └───────────────▲───────────────────────▲──────────────────┘
                         │ HTTPS (dial-out)      │ HTTPS (dial-out)
                         │                       │
              ┌──────────┴─────────┐   (any number of other nodes,
              │  Operator's        │    each own country/hardware)
              │  machine — agent   │
              │  daemon + own      │
              │  Ollama            │
              └────────────────────┘
```

- **Every node dials OUT to the coordinator** — nothing listens on an operator's
  machine, no inbound ports, no port-forwarding, no firewall holes. The coordinator is
  the only public surface, and it binds loopback by default (see Security below).
- **Each node's own country/egress is a real, different perspective** — a node
  researching from Berlin sees different sources than one in São Paulo; that's the
  **geo-diversity** moat a centralized API can't replicate.
- **Heterogeneous hardware (e.g. Mac Metal vs a VPS on CPU) is how model-identity gets
  verified** — see Trust & verification below.

## Portability (no provider lock-in)

The coordinator is **containerized and config-driven** — host/port/DB URL/secrets all
via environment (see `docker-compose.yml`'s `coordinator` service, or
[docs/network/SELF_HOST.md](network/SELF_HOST.md) for the bare-VPS recipe). It can move
to any rented host with **no code changes**. Persistence starts as SQLite (a single
file to move) and can swap to Postgres via the same config seam.

## Trust & verification (see DECISIONS D10)

- **Exact output-hash is out** — LLM inference isn't byte-reproducible across
  heterogeneous hardware.
- **A single fuzzy-agreement threshold is NOT enough to police model-identity.**
  Measured on real hardware (`gemma3:12b` Mac/Metal vs VPS/CPU): honest agreement floor
  **0.8473** overlapped the `gemma3:4b` downgrade ceiling **0.8495** — they're
  inseparable by one threshold. On easy prompts a smaller model's answer is genuinely
  fine anyway, so "downgrade" isn't even a cheat there.
- **So verification is QUALITY-based:** the judge scores every answer and pay is
  **score-weighted**, so a worse (e.g. downgraded) answer simply earns less;
  reputation gates new/low-trust nodes. This makes the economic design itself the
  verifier — no model-attestation needed for ordinary jobs.
- **When model-identity genuinely matters** (high-stakes jobs), reach for
  TEE/attestation or TOPLOC on *those* jobs specifically — not a global threshold.

## Live operator map (`GET /dashboard`)

The coordinator serves a self-contained dashboard (`passiveworkers/net/dashboard.py`) that
polls `/status` and renders the network on a Leaflet world map: each node positioned
by **country**, coloured by load, with model, **reputation**, last-seen, and recent job
flow. Country starts as self-reported; optional offline GeoIP verification (D43)
cross-checks it against the registration-time source IP and surfaces `geo_country` /
`geo_mismatch` — **never** exposing the raw IP. Default-off (needs an operator-supplied
GeoLite2 `.mmdb`), falling back to self-reported country when unavailable.

## Security posture

Full threat model, invariants, and known limitations now live in
**[SECURITY.md](../SECURITY.md)** — this used to be duplicated here and in the README;
it isn't anymore. In summary: two-layer auth (operator token + per-node secret),
loopback-by-default with a startup guard against weak public exposure, a fail-closed
ledger (settlement first, scores sanitized, conservation property-tested), no
info-leak/XSS in the dashboard, liveness-checked nodes so a dead worker can't wedge the
queue, and SSRF-guarded web research. Read SECURITY.md for the adversary-by-adversary
breakdown and the limitations we disclose rather than hide.
