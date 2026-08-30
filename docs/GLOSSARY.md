# Passive Workers — Glossary

Current vocabulary (Round 32 onward) first; deprecated pre-Round-32 terms are kept at
the bottom as pointers, not deleted — the round log in [ROADMAP.md](ROADMAP.md) still
uses the old names in its older entries.

## Single-player (`pw research`)

- **Research desk** — the single-player engine: `pw research` on the CLI, or `pw serve`
  as a local web UI at `http://127.0.0.1:8770`.
- **Planner** — discovers distinct angles on a brief before research starts (STORM-style
  query planning), so analysts investigate genuinely different questions, not the same
  one three times.
- **Analyst** — one local model researching one angle of the brief against the live web
  (or the local library with `--local`), producing sources and findings.
- **Blind judge** — scores each analyst's findings without knowing which model produced
  them, then merges them — keeping agreement, preserving each analyst's unique points,
  and surfacing outright disagreement instead of forcing a false consensus.
- **Editor** — compiles the judge's merged findings into one cited report
  (`./reports/*.md`, plus `--json`/`--html`).
- **Library** — your own indexed documents (`pw library add`), retrieved locally via
  hybrid dense+lexical search; cited as `[L#]`, kept separate from web sources `[S#]`.

## Network (opt-in, `pw join` / `pw ask`)

- **Coordinator** — the open-source, self-hostable hub holding the ledger, job queue,
  node registry, and telemetry (`council/net/coordinator_app.py`). Routes jobs and
  settles credit; never a token, never a proxy for traffic.
- **Operator** — a person who runs `pw join` and contributes their machine's compute.
- **Asker** — a person who submits a job and spends credit (`pw ask`).
- **Node** / **agent** — an operator's running worker process; dials out to a
  coordinator only, never accepts inbound connections.
- **Judge** (network sense) — a node that also scores other nodes' answers, blind, when
  it opts in via `--judge` (`PW_CAN_JUDGE=1`).
- **Assisted task** — a bounded, human-in-the-loop job (`pw tasks` / `pw accept` /
  `pw deliver`) for work that needs a real computer driven — an operator consents to
  one specific brief and does it themselves or with their own AI; the project's code
  never automates anyone's machine (see D18 in [DECISIONS.md](DECISIONS.md)).
- **Merge** — the network's judge-produced synthesis across nodes' answers, analogous
  to the single-player editor's report but assembled from independently-run nodes.
- **Credit** — the non-transferable internal unit, denominated in normalized compute
  units. Earned by helping, spent by asking. Not a token; no secondary market (D1).
- **Give/take rule** — a participant can't consume far beyond what they've contributed;
  blocks free-riding without needing a cash price.
- **Owned deliverable** — a result a node's own agent produced and may share — as
  opposed to *proxied traffic* (relaying someone else's packets), which is forbidden
  outright (D4).
- **Reputation** — a node's rolling average judge score; gates it into higher-trust
  work over time while leaving ungated work open to newcomers.
- **Lens** — an angle of attack given to an analyst/node via its prompt (e.g.
  `opportunity`, `skeptic`, `first_principles`, `practical`) to pull different ideas
  from the same or different models (applies uniformly to both chat jobs, `pw ask`,
  and research jobs, `pw research`/network research jobs — R38).
- **Model-downgrade** — the real cheat under a non-cash credit: a node silently running
  a smaller or more-quantized model than it claims, to earn credit for less work.
- **Cross-hardware verification** — confirming that honest answers from different
  hardware (e.g. Mac Metal vs a VPS CPU) agree by *meaning*, while a downgraded model
  is still caught — the empirical basis for quality-based (not hash-based)
  verification; see D10.
- **Geo-diversity** — the moat: authentic in-country web/research presence (a node's
  own internet egress) that centralized APIs can't replicate.
- **TPSO** — Third-Party Settlement Organization (e.g. Stripe Connect), the intended
  mechanism if/when real cash payouts ever exist, so KYC/AML sits with the processor,
  not this project.

## Deprecated / pre-Round-32 terms

Kept for history — the early round log still uses these. Never delete; map forward
when you see them:

- **The Council** — formerly: the whole project / the single-player engine (now
  "Passive Workers" / "the research desk").
- **Substrate** — formerly: the shared base (nodes + credit ledger + coordinator) that
  everything is built on (now just "the coordinator" + "the network").
- **Perspective** — formerly: one worker's/analyst's answer (now "analyst" in the
  single-player engine, or a node's answer in the network sense).
- **Worker** — formerly: a contributor's machine running one model (now "node" or
  "operator" in network docs; "analyst" in single-player docs — the split exists
  because the single-player and network senses of the old term had drifted apart).
