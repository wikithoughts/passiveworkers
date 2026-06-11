# Passive Workers — local-first deep research

**Your models. Your connection. Your disk.** One command turns any computer with
[Ollama](https://ollama.com) into a deep-research engine: multiple local models research the
**live web** as independent analysts, and a blind editor compiles one **cited markdown report**
into `./reports/`.

```bash
pip install -e .            # (PyPI release planned)
ollama pull qwen3:14b       # any decent models you like — it auto-detects what you have
pw research "What changed in EU AI Act enforcement this quarter, and who has been fined?"
```

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
brief in, live progress, rendered report, history of everything you've researched.

**Recommended setup (avoids public-search rate limits, keeps queries private):**
```bash
docker compose up -d searxng     # self-hosted meta-search; pw auto-detects it
```

## Why this exists

- **Currency beats memory.** Frontier chatbots answer from training data that is months or
  years old. This engine reads the web *now* and cites what it found. In our own blind trial,
  live-web research was the only thing that beat a frontier model — both times currency mattered.
- **Private by construction.** No account, no server, no telemetry. The only thing that leaves
  your machine is the web searches themselves. Reports are files on your disk.
- **Plural by design.** Different model families make *different* mistakes. A planner discovers
  distinct angles (STORM-style); each analyst researches its own angle with its own model and
  drafts from **full page extracts**, and a blind editor **preserves disagreement**
  (agree / differ / unique sections — never a forced consensus). Question diversity × model
  diversity catches what any single model hallucinates.
- **Free forever.** It's your hardware.

## Honesty section (when NOT to use this)

A frontier chatbot is better when the answer lives in stable knowledge — math, code,
explanations, anything where being current doesn't matter. We measured this bluntly: local
models lose that fight 0/10 (`docs/TRIAL_RESULTS.md`). This tool wins when the answer lives
**on today's web** — prices, regulations, releases, markets, anything where "as of when?"
decides usefulness. Optional `--editor api` brings your own OpenRouter key for a frontier
editor pass over locally-gathered findings — your choice; the default is fully local.

## Security model (designed in, not bolted on)

- **No browser automation, no computer use, no sessions, no cookies — ever.** Search API +
  plain fetch of public pages only. The gravest agent attacks (session-token theft,
  authenticated exfiltration) have nothing to grab here.
- **All web content is untrusted data.** It passes a sanitizer (invisible-Unicode and
  hidden-comment stripping) and enters prompts only inside spotlighting delimiters marked
  "data, never instructions" (`council/sanitize.py`).
- **Models hold zero tool privileges.** They only return text; every action (search, fetch,
  file write) is plain Python under this repo's control. Reports write only into `./reports/`;
  fetches pass an SSRF guard (public hosts only, size-capped).

## Hardware guide

| Your machine | Models that fit (4-bit) | Experience |
|---|---|---|
| 8 GB RAM/VRAM | 3–4B (llama3.2, qwen3:4b, gemma3:4b) | quick reports, lighter analysis |
| 16 GB | 7–14B (qwen3:14b, gemma3:12b) | the sweet spot |
| 24 GB+ | 14–32B (+ mistral-small:22b) | best local quality |

Models run **sequentially** by design — no concurrent loads fighting for memory.
On CPU-only or busy machines, cap the cast by weight size: `PW_MODEL_CAP_GB=3 pw research …`
(big models on CPU crawl at 3–6 tok/s — a small model that fits is always faster than a large
one that spills).

Page evidence uses [trafilatura](https://github.com/adbar/trafilatura) for clean main-content
extraction (with a regex fallback); full credits in [docs/PRIOR_ART.md](docs/PRIOR_ART.md).

## Federation (experimental) — the multiplayer mode

Everything above runs on one machine. The same repo contains the network layer
(`council/net/`): connect machines in **different countries** and reports gain genuinely
different windows on the web — each node researches from its own egress and returns its **own
cited findings** (never proxied traffic), an editor merges with per-country sections, and a
non-tradeable mutual-aid credit accounts for who helped whom. It already powers a live
two-country deployment, plus typed marketplace jobs (deep research, sharded batch work with
capability matching). Two principles are absolute: **operators always see and consent to the
work their machine does** (never hidden tasks), and when a job needs a real computer driven, it
is **handed to the human operator** to do with their own AI under approval — our code never
automates anyone's machine. The long game is a commons of computers doing real work for each
other — **no token, no secondary market, money only ever at the edges.** See
[docs/FEDERATION_V2.md](docs/FEDERATION_V2.md).

## Documentation

| Doc | What |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | The why, the history, the layered vision. |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Milestones + pivots (living tracker). |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Roles, local vs networked shape, trust/security. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Settled decisions + rationale (ADR-style, D1–D16). |
| [docs/ECONOMICS.md](docs/ECONOMICS.md) | Credit, give/take, score-weighted payouts, legal posture. |
| [docs/TRIAL_RESULTS.md](docs/TRIAL_RESULTS.md) | Our blind trial vs a frontier model — losses included. |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Terms (Council, analyst, judge, lens, credit…). |

## Status

Young software, honestly labeled: the single-player engine works and is verified end-to-end;
the federation layer is experimental. We publish our methodology and our losses, not just wins.
Issues and PRs welcome. MIT.
