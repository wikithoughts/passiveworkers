# Passive Workers — Architecture

> How the system is built. Pairs with [DECISIONS.md](DECISIONS.md) (why) and the code in
> `council/`. Terms in **bold** are defined in [GLOSSARY.md](GLOSSARY.md).

## Roles

- **Asker** — submits a question/task and spends credit.
- **Worker** — a contributor's machine running one model with a **lens** and a **country** tag;
  returns an **owned answer** (never proxied traffic).
- **Judge** — scores the candidate answers **blind** (ideas compete) and **merges** them into a
  superior, diversity-preserving synthesis.
- **Coordinator** — the open-source, self-hostable hub: holds the **ledger**, the job queue, the
  node registry, and telemetry. Routes *tasks* and settles *credit*; never a token, never traffic.

## Current shape (M1 — local, in-process)

```
run_demo → Council(coordinator) ──fan-out──▶ PerspectiveWorker × N  (local Ollama models)
                                  ──score+merge──▶ Judge
                                  ──settle──▶ Ledger (in-memory)
```
All in one process on the Mac. Diversity = different local models + lenses. Files:
`council/coordinator.py`, `worker.py`, `judge.py`, `ledger.py`, `run_demo.py`.

## Target shape (M2 — networked two machines)

```
        ┌─────────────── VPS (different country) ───────────────┐
        │  Coordinator service (HTTP/HTTPS)                      │
        │   • ledger + job queue + node registry + telemetry    │
        │   • SQLite (→ Postgres later), config via env          │
        │   • token-auth node registration, TLS                  │
        │  + Worker daemon (small Ollama models, CPU)            │
        └───────────────▲───────────────────────▲───────────────┘
                         │ HTTPS (dial-out)      │ HTTPS (dial-out)
                         │                       │
              ┌──────────┴─────────┐   (more nodes later)
              │  Mac (Metal)       │
              │  Worker daemon     │
              │  + Judge (larger   │
              │    models)         │
              └────────────────────┘
```

- **Both machines dial OUT to the coordinator** — no inbound to the Mac (no port-forwarding,
  no firewall holes). The coordinator is the only public surface.
- **The VPS in a different region is the first real geo-diverse perspective.** It's likely
  CPU-only → runs small models → a genuinely different voice in the council.
- **Mac (Metal) vs VPS (CPU)** is exactly the heterogeneous pair needed to finally measure the
  **cross-hardware verification** floor vs. the model-downgrade ceiling (the open Spike-1 gap).

## Portability (no provider lock-in)

The coordinator is **containerized and config-driven** — host/port/DB URL/secrets all via
environment. It starts on the current VPS but can be relocated to any rented host with **no code
changes**. Persistence starts as SQLite (a single file to move) and can swap to Postgres via the
same config seam. This keeps us free to move to cheaper/closer/rented resources anytime.

## Trust & verification (revised by the M2 measurement — see DECISIONS D10)

- **Exact output-hash is out** — LLM inference isn't byte-reproducible across heterogeneous hardware.
- **A single fuzzy-agreement threshold is NOT enough to police model-identity.** Measured on real
  hardware (`gemma3:12b` Mac/Metal vs VPS/CPU): honest agreement floor **0.8473** overlapped the
  `gemma3:4b` downgrade ceiling **0.8495** — they're inseparable by one threshold. On easy prompts a
  smaller model's answer is genuinely fine anyway, so "downgrade" isn't even a cheat there.
- **So verification is QUALITY-based:** the judge scores every answer and pay is **score-weighted**, so
  a worse (e.g. downgraded) answer simply earns less; reputation gates new/low-trust nodes. This makes
  the economic design itself the verifier — no model-attestation needed for ordinary jobs.
- **When model-identity genuinely matters** (high-stakes jobs), reach for TEE/attestation or TOPLOC on
  *those* jobs specifically — not a global threshold.

## Live operator map (`GET /dashboard`)

The coordinator serves a self-contained dashboard (`council/net/dashboard.py`) that polls `/status`
and renders the network on a Leaflet world map: each node positioned by **country**, coloured by load,
with model, **reputation**, last-seen, and the recent job flow. It's the "see the network breathing"
view. Country starts as self-reported, but **offline GeoIP verification shipped (D43)**: the coordinator
checks a node's self-reported country against the source IP it saw at registration and surfaces the
result as `geo_country` / `geo_mismatch` — **never** exposing the raw IP. It's default-off (needs an
operator-supplied GeoLite2 `.mmdb`) and falls back to self-reported country when unavailable. Still
ahead: animated asker→worker→judge arcs as jobs flow.
Reputation shown here is the same rolling mean judge score that drives fleet selection — so the map
also shows *who is trusted*, not just who is online.

## Security posture (hardened — see DECISIONS D11)

- **Two-layer auth.** A shared **operator token** (`X-PW-Token`) gates every write endpoint; a
  **per-node secret** (`X-Node-Secret`, minted at register, only its hash stored) authenticates node
  operations. A node is identified *from its secret* and may only complete its **own** tasks — so a
  token-holding node cannot hijack another's judge task, forge scores, or drain the ledger.
- **Loopback by default + startup guard.** The coordinator binds `127.0.0.1`; binding a public
  interface with a weak/empty token is refused at startup. Expose only behind a tunnel/reverse proxy.
- **Fail-closed ledger.** Settlement runs first; scores are sanitized (non-finite/out-of-range → 0,
  empty/errored answers → 0 with no reputation); an over-budget job fails cleanly. Conservation holds
  on every path (property-tested incl. inf/NaN).
- **No info leak / no XSS.** `/status` exposes an opaque `node_key`, never raw `node_id`/IP; the
  dashboard HTML-escapes every node-supplied field (blocks stored XSS via a malicious node name).
- **Liveness ≠ work.** Agents heartbeat on a background thread; a reaper fails jobs whose assigned
  node goes stale or that exceed the run deadline — a dead worker can't wedge the queue.
- Workers execute only declared, structured task types (no arbitrary remote code); contributor
  machines only dial out (no inbound). Web research (M4) is SSRF-guarded and returns owned findings.
- Secrets via env / 1Password (`op`), never committed; the SSH key is used directly (agent-independent).
