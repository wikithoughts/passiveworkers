# Passive Workers — local-first deep research

**Your models. Your connection. Your disk.** One command turns any computer with
[Ollama](https://ollama.com) into a deep-research engine: multiple local models research the
**live web** as independent analysts, and a blind editor compiles one **cited markdown report**
into `./reports/`.

```bash
pip install 'passiveworkers[all]'   # core + extraction + private-docs + MCP
ollama pull qwen3:14b               # any decent models you like — it auto-detects what you have
pw research "What changed in EU AI Act enforcement this quarter, and who has been fined?"
```

> From source instead: `git clone https://github.com/wikithoughts/passiveworkers && pip install '.[all]'`

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

### Research your own documents too (private, local RAG)

```bash
pw library add ~/Documents/contracts        # index files or folders (PDF, Word, txt, md)
pw research "What are the renewal terms across my contracts?" --local   # docs only
pw research "How do my notes compare to the latest guidance?"           # docs + live web (default)
```
Your files are chunked and embedded **locally** (Ollama `nomic-embed-text`) into
`~/.passiveworkers/library.db` — nothing is uploaded. Reports cite documents as `[L#]` and web
sources as `[S#]`, kept in separate sections.

Retrieval is state-of-the-art but lean: **hybrid** (dense embeddings ⊕ BM25 lexical, fused by
reciprocal rank fusion) so exact names/codes/numbers aren't missed; **structure-aware chunking**
that never straddles a section; **parent-window** expansion for grounding; and optional
**Contextual Retrieval** (`PW_CONTEXTUAL_CHUNKS=1`, Anthropic's technique — a small local model
situates each chunk before indexing) and **reranking** (`PW_RERANK=1`). Indexing is incremental
(unchanged files are skipped). Measure it on your own corpus with `python scripts/bench_rag.py` —
we publish what actually helps, not vendor numbers.

### Use it from your own AI (MCP)

```bash
pw mcp        # run as an MCP server (stdio)
```
Add to Claude Desktop's `claude_desktop_config.json` so your assistant can call the engine:
```json
{ "mcpServers": { "passive-workers": { "command": "pw", "args": ["mcp"] } } }
```
Tools exposed: `research`, `library_search`, `library_add`. Your own agentic AI orchestrates;
our multi-model, live-web + private-library engine is the capability it reaches for.

**Recommended setup (avoids public-search rate limits, keeps queries private):**
```bash
docker compose up -d searxng     # self-hosted meta-search; pw auto-detects it
```

## Why this exists

- **Currency beats memory.** Frontier chatbots answer from training data that is months or
  years old. This engine reads the web *now* and cites what it found. In our own blind trial,
  live-web research was the only thing that beat a frontier model — both times currency mattered.
  For time-sensitive questions it leads with the **freshest-dated** sources (so they survive the
  cap and get read first), pins the **current year into the search query** so the engine returns
  *this year's* results instead of an SEO-dominant old page, and **researches deeper on breaking
  topics** — while leaving stable-fact questions in plain relevance order.
- **Private by construction.** No account, no server, no telemetry. The only thing that leaves
  your machine is the web searches themselves. Reports are files on your disk.
- **Plural by design.** Different model families make *different* mistakes. A planner discovers
  distinct angles (STORM-style); each analyst researches its own angle with its own model and
  drafts from **full page extracts**, and a blind editor **preserves disagreement**
  (agree / differ / unique sections — never a forced consensus). Question diversity × model
  diversity catches what any single model hallucinates.
- **Right source for the query.** Beyond live web, academic-looking queries also hit **arXiv** and
  definitional ones **Wikipedia** (`PW_SOURCE_ROUTING=off` to disable). Models stay warm between
  steps (`PW_OLLAMA_KEEP_ALIVE`, default `30m`; set `0` to unload immediately) so there are no
  reload stalls mid-run.
- **Free forever.** It's your hardware.

## Honesty section (when NOT to use this)

A frontier chatbot is better when the answer lives in stable knowledge — math, code,
explanations, anything where being current doesn't matter. We measured this bluntly: local
models lose that fight 0/10 (`docs/TRIAL_RESULTS.md`). This tool wins when the answer lives
**on today's web** — prices, regulations, releases, markets, anything where "as of when?"
decides usefulness. Optional `--editor api` brings your own OpenRouter key for a frontier
editor pass over locally-gathered findings — your choice; the default is fully local.

## Benchmark (honest, small sample)

On a 25-question subset of OpenAI's SimpleQA, the engine scored **64%** (single `qwen2.5:14b`,
snippet-only search, LLM-graded — `scripts/bench_simpleqa.py`). Context, plainly: SimpleQA rewards
short factoid recall, which is the *opposite* of what this tool is built for (multi-source reports
where currency and citation matter); the leaders' ~95% figures use bigger models, deeper agentic
loops, and more sources. We publish the number — small sample and all — because the honest floor is
more useful to you than a cherry-picked one. Run it yourself: `python scripts/bench_simpleqa.py --n 100`.

### Citation fidelity (the metric that actually matters here)

A research tool lives or dies on one question: *when it says X [S3], does source S3 say X?*
`scripts/eval_citation_fidelity.py` measures exactly that — for every cited claim it checks
content-overlap with the source it points at and flags numbers stated in a claim that are absent from
the source. Two keyless (no-API-cost) modes:

```bash
python scripts/eval_citation_fidelity.py --report reports/your-report.md   # score an existing report (re-fetches its sources)
python scripts/eval_citation_fidelity.py --run --depth quick               # fresh run, scored against the exact extract each model read
```

It is honest about being a **floor**: lexical grounding catches off-topic citations and fabricated
numbers — the common, damaging failures — but a GROUNDED verdict means "not obviously fabricated",
*not* "verified true" (it can't detect subtle misrepresentation). The "grounded rate" is of
*verifiable* claims; unreachable sources are reported separately, never counted as failures.

### Currency gap (where live web beats a frontier model's memory)

This tool's real edge isn't raw model size — it's *currency*. `scripts/eval_currency_gap.py` measures
exactly that: the local council (live web) vs a frontier model answering from its frozen training
knowledge, scored against curated references, as a matrix by *currency window × category*. A `static`
control set keeps it fair (where currency is irrelevant, the frontier should win). It **spends nothing
by default** — a bare run is a `$0` dry run that validates the question set and estimates cost; only
`--run` (with `OPENROUTER_API_KEY` in your env) makes the paid frontier calls:

```bash
python scripts/eval_currency_gap.py            # dry run — validate + estimate, $0
python scripts/eval_currency_gap.py --run      # paid: council (free) vs frontier (your API key)
```

## Security model (designed in, not bolted on)

- **No browser automation, no computer use, no sessions, no cookies — ever.** Search API +
  plain fetch of public pages only. The gravest agent attacks (session-token theft,
  authenticated exfiltration) have nothing to grab here.
- **All web content is untrusted data.** It passes a sanitizer (invisible-Unicode and
  hidden-comment stripping) and enters prompts only inside spotlighting delimiters marked
  "data, never instructions" (`council/sanitize.py`). The same gate covers the **ends** of the
  pipeline too: your brief is scrubbed of hidden/bidi characters and length-bounded before it
  shapes any prompt, and every model-written passage is re-scrubbed before it lands in the
  report (so a payload smuggled through a source can't survive into the artifact) — all without
  touching visible layout or citations.
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
capability matching, and **assisted** human-in-the-loop tasks — `pw tasks` / `pw accept` /
`pw deliver`: an operator consents to a bounded brief and does it with their own AI or by hand,
never our autonomous code). The asker **rates** the result (`pw rate`), building operator
**reputation** that gates higher-trust offers — while newcomers can still take ungated work. Deliverables can be **real files**, moved as content-addressed,
integrity-verified chunks (`pw deliver <task> @file <job>` → `pw fetch <job> <dir>`) — a
corrupted or swapped chunk is detected, never written. With the `[crypto]` extra, deliverables are
**signed** (the asker verifies which operator produced them) and files can be **end-to-end
encrypted** to the asker (`pw keygen` → the coordinator relays ciphertext it cannot read). For
authenticity that holds even against a hostile coordinator, the asker **pins** an operator's signing
key out of band — `pw fingerprint` (operator) → `pw trust add` (asker), or trust-on-first-use — and
`pw fetch` verifies against the pinned key, refusing a swapped one. Two principles are absolute: **operators always see and consent to the
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
| [docs/CONTRIBUTE_COMPUTE.md](docs/CONTRIBUTE_COMPUTE.md) | Plug a machine into the federation — what it does, earns, and why it's safe. |
| [docs/RELEASING.md](docs/RELEASING.md) | How to publish to PyPI (verified build; needs your token). |

## Status

Young software, honestly labeled: the single-player engine works and is verified end-to-end;
the federation layer is experimental. We publish our methodology and our losses, not just wins.
Issues and PRs welcome. MIT.
