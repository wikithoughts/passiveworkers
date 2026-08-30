# Prior art & credits

Passive Workers stands on the shoulders of an open ecosystem. We **reuse permissively-licensed
libraries directly** and **reimplement published techniques** in our own idiom. We do not fork or
vendor the orchestration of the projects below — our differentiator is the multi-*model*
dissent-preserving council, the federation layer, and the injection-tested security stance, which
we own end to end. (See `docs/DECISIONS.md` D18 for the reuse policy.)

## Embedded libraries (used directly)

- **[trafilatura](https://github.com/adbar/trafilatura)** (Apache-2.0) — main-content + metadata/date
  extraction from HTML, behind `council/research.fetch_extract` (graceful regex fallback when absent).
- **[ddgs](https://github.com/deedy5/ddgs)** — DuckDuckGo metasearch client.
- **[SearXNG](https://github.com/searxng/searxng)** (AGPL-3.0) — self-hosted metasearch, run as a
  separate service via `docker-compose.yml` and called over HTTP (not linked into our code).
- FastAPI, Uvicorn, Pydantic, Requests, psutil — standard infrastructure.

## Techniques we learned from (reimplemented, not copied)

- **[Stanford STORM](https://github.com/stanford-oval/storm)** (MIT) — perspective-guided question
  asking. Our "STORM-lite" planner (`council/local.py`) assigns each analyst a distinct angle.
- **[gpt-researcher](https://github.com/assafelovic/gpt-researcher)** (Apache-2.0) — planner +
  parallel execution and drafting from full pages, not snippets (our `fetch_extract` page evidence).
  Their MCP server is the model for our planned MCP interop.
- **[local-deep-research](https://github.com/LearningCircuit/local-deep-research)** (MIT) —
  fully-local research with adaptive engine selection and SearXNG; their private-documents RAG is on
  our roadmap.

## What the wider ecosystem confirms

- **The "floor, not verified-true" framing is externally validated.** ["Cited but Not
  Verified"](https://arxiv.org/abs/2605.06635) (arXiv:2605.06635, May 2026) benchmarked frontier
  deep-research agents and found only 39–77% factual/citation accuracy — and accuracy *drops* as
  tool-call count scales up. `council/fidelity.py` (D27) already refuses to overclaim: it's an
  explicit lexical **grounding floor** (content-token overlap plus fabricated-number detection), and
  a GROUNDED verdict is documented as "not obviously fabricated," never "verified true." This paper
  is independent confirmation that the framing is the honest stance, not an undersell — if
  well-resourced frontier systems can't clear the bar of "verified," a tool that states plainly it
  measures a floor is more trustworthy than one that implies otherwise.
- **No comparable non-token peer network exists.** Among local/decentralized-compute-sharing
  projects we surveyed — [GaiaNet](https://github.com/GaiaNet-AI) (added staking/validator
  incentives in 2026), [Petals](https://github.com/bigscience-workshop/petals) (BitTorrent-style LLM
  sharding with no incentive layer at all, flagged in research literature as vulnerable to
  free-riding without one), and SwarmHarness ([arXiv:2605.28764](https://arxiv.org/abs/2605.28764), a
  2026 academic proposal for token/incentive-aligned agent networks) — none implement a
  non-transferable, no-token, earned-only credit model. D1 in `docs/DECISIONS.md` commits us to
  exactly that: internal, non-transferable credit, no tradeable token, ever. Worth stating explicitly
  rather than leaving implicit: as far as this survey found, that combination is a real positioning
  differentiator, not just a self-imposed constraint.

Thank you to these authors and communities.
