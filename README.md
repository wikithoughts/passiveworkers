# Passive Workers

[![CI](https://github.com/wikithoughts/passiveworkers/actions/workflows/ci.yml/badge.svg)](https://github.com/wikithoughts/passiveworkers/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/passiveworkers)](https://pypi.org/project/passiveworkers/)
[![Python](https://img.shields.io/pypi/pyversions/passiveworkers)](https://pypi.org/project/passiveworkers/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**The research engine you own.** Many local [Ollama](https://ollama.com) models
research the live web as independent analysts, and a blind editor writes one cited
report that keeps their disagreements instead of forcing a false consensus. That's the
single-player half, and it's the product — judge it on that. Opt-in, the same engine
is also a commons: idle computers doing research for each other, no token and no cloud
in the middle ([why](docs/VISION.md)).

<!-- TODO: hero GIF via vhs, see docs/REVIEW_2026-07.md R29 -->

**Prerequisite:** Python 3.10+ and [Ollama](https://ollama.com/download) installed and
running (it serves the models locally — nothing here calls a cloud API by default).

```bash
pip install 'passiveworkers[all]'   # core + extraction + private-docs + MCP
ollama serve &                      # make sure Ollama is running (skip if it already is)
ollama pull qwen3:14b               # any decent models you like — it auto-detects what you have
pw status                           # ✓ Ollama up? which models? library? — a 1-second preflight
pw research "What changed in EU AI Act enforcement this quarter, and who has been fined?"
```

> From source instead: `git clone https://github.com/wikithoughts/passiveworkers && pip install '.[all]'`
> — or point an AI assistant at [docs/INSTALL.md](docs/INSTALL.md) and let it drive.

```
🔬 Deep research (standard) — analysts: qwen3:14b, gemma3:12b, llama3.2 · editor: qwen3:14b
  [1/3] qwen3:14b researching the live web…
      12 sources · 390 words · 41s
  [2/3] gemma3:12b researching the live web…
  ...
  blind judge + editor compiling the report…
📄 Report ready in 7.2 min · 1480 words · 31 sources → reports/2026-06-10-eu-ai-act….md
```

Prefer a UI? **`pw serve`** → a single-user research desk at `http://127.0.0.1:8770` —
brief in, live progress, rendered report, history of everything you've researched. See
it (and the operator map, and the marketplace UI) without installing anything: the live
evidence site at **[wikithoughts.github.io/passiveworkers](https://wikithoughts.github.io/passiveworkers/)**,
or the screenshots in [docs/preview/](docs/preview/).

## What leaves your machine

| Data | Single-player | Network (opt-in) |
|---|---|---|
| Your documents | **never leave** | **never leave** |
| Your brief | never by default (`--editor api` is the one opt-in exception) | → coordinator; the default `chat` job type also sends it verbatim as the search query |
| Search queries | → your chosen backend (DDG default; self-hosted SearXNG; keyed = central) | same, from each node's own egress |
| Reports / deliverables | local disk only | signed; optionally end-to-end encrypted |
| Telemetry / accounts | **none** | heartbeat + credit ledger to your coordinator |

Full threat model, adversary-by-adversary: **[SECURITY.md](SECURITY.md)**.

## How it works

```text
Single-player  ·  pw research "…"
   brief ─▶ planner ─▶ N distinct angles
        ├─▶ analyst A (own model, own angle) ─┐   each researches the LIVE WEB
        ├─▶ analyst B (own model, own angle) ─┼─▶ blind judge: scores + MERGE
        └─▶ analyst C (own model, own angle) ─┘     (keeps agree / differ / unique)
                                                 └─▶ editor ─▶ one cited report
                                                       → ./reports/*.md · --json · --html
Network (opt-in)  ·  pw ask "…" / pw join <url> <token>
   asker ─▶ coordinator ─▶ splits the job across worker nodes (each researches from its
   own country / egress) ─▶ judge ─▶ reassembled, cited, credit-settled deliverable
```

Models hold **zero tool privileges** — they only return text; Python does every
search, fetch, and file write.

## What's stable vs what's maturing

| Surface | Status |
|---|---|
| `pw research`, `pw library`, `pw serve`, `pw mcp` | **stable** — the flagship, verified end-to-end |
| Network: joining a coordinator (`pw join`), asking (`pw ask`) | **working, invite-only** while it hardens |
| Self-hosting your own coordinator | **working** — [docs/network/SELF_HOST.md](docs/network/SELF_HOST.md) |
| Assisted (human-in-the-loop) tasks | **experimental** — real, tested, early |

## Why this exists

- **Currency beats memory.** A frontier chatbot answers from training data that is
  months or years old. This engine reads the web *now* and cites what it found — in
  our own blind trial, live-web research was the only thing that beat a frontier
  model, and both wins were currency wins (`docs/TRIAL_RESULTS.md`).
- **Plural by design.** A planner discovers distinct angles (STORM-style); each
  analyst researches its own angle with its own model from full page extracts, and a
  blind editor **preserves disagreement** — agree / differ / unique sections, never a
  forced consensus. Model diversity catches what any single model hallucinates.
- **Private by construction.** No account, no server, no telemetry. By default, the
  only thing that leaves your machine is the search terms themselves (the one
  documented exception: `--editor api` sends your brief to your own configured
  external API — opt-in only). Reports are files on disk.
- **Made to share, opt-in.** Idle compute is wasted compute. The same engine is a
  commons where machines do bounded jobs for each other, returning work they produced,
  never proxied traffic — you choose the kinds of work your machine accepts when you
  join, every task it runs is visible in the log, and sensitive work is never auto-run
  without a human consenting to that one task (see [Two real doors in](#the-network--two-real-doors-in-today) below).

### Research your own documents too (private, local RAG)

```bash
pw library add ~/Documents/contracts        # index files or folders (PDF, Word, txt, md)
pw research "What are the renewal terms across my contracts?" --local   # docs only
```
Your files are chunked and embedded **locally** (Ollama `nomic-embed-text`) into
`~/.passiveworkers/library.db` — nothing is uploaded. Reports cite documents as `[L#]`
and web sources as `[S#]`, kept in separate sections. Retrieval is hybrid (dense ⊕
BM25 lexical, fused by reciprocal rank fusion), structure-aware, with optional
Contextual Retrieval (`PW_CONTEXTUAL_CHUNKS=1`) and reranking (`PW_RERANK=1`) —
measure it on your own corpus with `python scripts/bench_rag.py`.

### Use it from your own AI (MCP)

```bash
pw mcp        # run as an MCP server (stdio)
```
```json
{ "mcpServers": { "passive-workers": { "command": "pw", "args": ["mcp"] } } }
```
Tools exposed: `research`, `library_search`, `library_add`. Your own agentic AI
orchestrates; this multi-model, live-web + private-library engine is the capability it
reaches for.

## How it compares

| | Passive Workers | GPT Researcher | Local Deep Research | Perplexity | Petals / Exo |
|---|---|---|---|---|---|
| Runs fully local, no API key | ✅ | optional | ✅ | ❌ cloud | ✅ |
| Nothing leaves but the web searches | ✅ | depends on LLM | ✅ | ❌ | ✅ |
| Multi-model council + **preserved dissent** | ✅ | ✖ single agent | ✖ | ✖ | n/a |
| Live-web currency + cited report | ✅ | ✅ | ✅ | ✅ | ✖ |
| Web search backends | DDG · SearXNG · Brave/Tavily/Serper · arXiv/Wikipedia | keyed engines | **10+ engines** | own index | ✖ |
| Private-document RAG | ✅ | ✅ | ✅ | limited | ✖ |
| Opt-in compute network **with incentives** | ✅ credits + reputation | ✖ | ✖ | ✖ | shards 1 model, **no incentive layer** |
| Price | free (your hardware) | API $/run | free | subscription | free |

Where others lead today, plainly: **GPT Researcher** has more export formats and a
recursive breadth/depth tree; **Local Deep Research** wires in more search engines;
**Perplexity** is faster on a bigger model. Our bet is the combination nobody else
makes — local privacy + multi-model dissent + live-web currency + an opt-in commons.
("Nothing leaves but the web searches" describes the default path; the one opt-in
exception, `--editor api`, is disclosed in [SECURITY.md](SECURITY.md).)

## Receipts (we publish losses, not just wins)

- **When NOT to use this**: a frontier chatbot wins when the answer lives in stable
  knowledge (math, code, explanations) — local models lose that fight **0/10**
  (`docs/TRIAL_RESULTS.md`). This tool wins when the answer lives on *today's* web.
- **SimpleQA**: 25-question subset, **64%** (single `qwen2.5:14b`, LLM-graded,
  `scripts/bench_simpleqa.py`) — SimpleQA rewards short factoid recall, the opposite of
  what this is built for; leaders' ~95% use bigger models and deeper agentic loops.
- **Citation fidelity** (the metric that matters here): does source S3 actually say
  what claim `[S3]` says it says? `scripts/eval_citation_fidelity.py` checks
  content-overlap and flags numbers absent from the source. It's an honest floor — a
  GROUNDED verdict means "not obviously fabricated," not "verified true."
- **Currency gap**: `scripts/eval_currency_gap.py` measures live-web research vs a
  frontier model's frozen memory, by currency window × category, `$0` by default
  (only `--run` spends your `OPENROUTER_API_KEY`). Full methodology and numbers:
  [docs/BENEFIT.md](docs/BENEFIT.md).

## Security model (designed in, not bolted on)

- **No browser automation, no computer-use, no sessions, no cookies — ever.**
- **All web content is untrusted data** — sanitized and spotlighted ("data, never
  instructions") before it can reach a prompt or a report (`council/sanitize.py`).
- **Models hold zero tool privileges.** Every action is plain Python under this
  repo's control; reports write only into `./reports/`; fetches are SSRF-guarded.
- **Your keys never leave your device.**

Full threat model, disclosed limitations, and vulnerability reporting:
**[SECURITY.md](SECURITY.md)**.

## Hardware guide

| Your machine | Models that fit (4-bit) | Experience |
|---|---|---|
| CPU-only (no GPU) | 3–4B, `PW_MODEL_CAP_GB=3` | works, slow (~3–6 tok/s) |
| 8 GB RAM/VRAM | 3–4B (llama3.2, qwen3:4b) | quick reports |
| 16 GB | 7–14B (qwen3:14b, gemma3:12b) | the sweet spot |
| 24 GB+ | 14–32B (+ mistral-small:22b) | best local quality |

Models run **sequentially** by design — no concurrent loads fighting for memory.

## The network — two real doors in, today

Everything above runs on one machine. The same repo also has a commons where machines
do typed jobs for each other (`council/net/`) — working, still maturing. Two doors in,
both real right now:

1. **Self-host a cell.** No invite needed — run your own coordinator and bring your
   own operators. [docs/network/SELF_HOST.md](docs/network/SELF_HOST.md).
2. **Join an existing coordinator.** The maintainer's own coordinator is invite-only
   while it hardens — [docs/CONTRIBUTE_COMPUTE.md](docs/CONTRIBUTE_COMPUTE.md) to
   contribute a machine, [docs/network/ASKING.md](docs/network/ASKING.md) to submit
   work to one (`pw ask`). No invite yet? Use the
   [join-the-network waitlist](https://github.com/wikithoughts/passiveworkers/issues/new?template=join-the-network.yml)
   — every request gets a reply.

Two invariants hold regardless of which door: **you choose the kinds of work your
machine accepts when you join** (research, judging, batch, assisted) — every task it
runs is visible in the log, and you can stop it at any time; **sensitive work
(anything touching a real computer via `assisted`) is never auto-run** — a human
always sees the brief and consents to that one task (D53). No token, no secondary
market, money only ever at the edges. Deeper design:
[docs/FEDERATION_V2.md](docs/FEDERATION_V2.md).

## We work with the ledger open

Every round of work is logged — what shipped, what we tried and reverted, and why —
in [docs/ROADMAP.md](docs/ROADMAP.md). Every non-obvious architectural choice, with
the alternatives we rejected and the reasoning, is in
[docs/DECISIONS.md](docs/DECISIONS.md). Our own trial losses are in
[docs/TRIAL_RESULTS.md](docs/TRIAL_RESULTS.md). Verify us; don't take our word for it.

## Documentation

| For | Docs |
|---|---|
| **Users** | [USE_CASES.md](docs/USE_CASES.md) (who this helps, 15 scenarios) · [VISION.md](docs/VISION.md) (why) |
| **Network operators** | [CONTRIBUTE_COMPUTE.md](docs/CONTRIBUTE_COMPUTE.md) · [network/SELF_HOST.md](docs/network/SELF_HOST.md) · [network/ASKING.md](docs/network/ASKING.md) |
| **Contributors** | [CONTRIBUTING.md](CONTRIBUTING.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [GLOSSARY.md](docs/GLOSSARY.md) · [ROADMAP.md](docs/ROADMAP.md) · [RELEASING.md](docs/RELEASING.md) |
| **Skeptics** | [DECISIONS.md](docs/DECISIONS.md) · [ECONOMICS.md](docs/ECONOMICS.md) · [TRIAL_RESULTS.md](docs/TRIAL_RESULTS.md) · [SECURITY.md](SECURITY.md) |
| **AI assistants** | [llms.txt](llms.txt) · [INSTALL.md](docs/INSTALL.md) · [CLAUDE.md](CLAUDE.md) |

## Status

Young software, honestly labeled: the single-player engine works and is verified
end-to-end; the network layer is the maturing next track. We publish our methodology
and our losses, not just wins. Issues and PRs welcome. MIT.
