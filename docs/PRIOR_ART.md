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

Thank you to these authors and communities.
