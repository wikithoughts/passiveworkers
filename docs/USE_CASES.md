# Passive Workers — use cases: how this helps people

Passive Workers exists to put real research and compute power back in people's own hands — private,
free, and honest. Below are concrete ways it helps, each with the exact command and the benefit.

**Honest framing first.** A frontier chatbot is still better for *stable* knowledge (math, code,
explanations) — our own blind trial says so. Passive Workers wins on **privacy, cost, currency,
sovereignty, honest citations, and collective compute** — and on letting people who *can't* or
*shouldn't* send their data to a cloud do serious research anyway. The opt-in **network** is the
maturing next track (invite-only today); the single-player research engine is the verified flagship.
Every example below stays inside the project's hard rules: nothing leaves your machine but web
searches, nodes return **owned deliverables** (never proxied traffic), and there is **no
computer-use** of anyone's machine.

---

## Privacy & confidentiality — research without surrendering your data

**1. A lawyer checks live regulation against privileged files.** AI-tool chats have been ruled
discoverable and carry no attorney–client privilege — so privileged material can't go to a cloud
model. Here it never does.
```bash
pw library add ~/Matters/AcmeCorp        # privileged contracts/memos — indexed locally, never uploaded
pw research "How do this quarter's EU AI Act enforcement actions affect our SaaS clauses?"
```
The report blends your documents (`[L#]`) with the live web (`[S#]`) in separate, labeled sections;
only neutral web-search terms leave the machine.

**2. A clinician or therapist synthesizes new guidance against private case notes.** PHI stays on the
device (HIPAA-style data residency); cloud therapy chats have "no legal confidentiality."
```bash
pw library add ~/clinic/case-notes
pw research "How does the newest treatment guidance compare with the approaches in my notes?"
```

**3. An investigative journalist works with confidential source material.** Source documents stay
local; there are no cookies or sessions to seize, and a whole corpus can be analyzed fully offline.
```bash
pw library add ~/investigation/leaked-docs
pw research "Cross-reference these documents against public filings and recent reporting." --local
```

**4. A founder / R&D engineer researches against proprietary specs.** Trade secrets are never
transmitted; with self-hosted search even the *queries* stay private.
```bash
docker compose up -d searxng     # self-hosted meta-search (pw auto-detects it)
pw library add ~/product/specs
pw research "How does our roadmap compare to competitors' shipped features this year?"
```

## Access & cost — serious research with no API budget

**5. A grad student with no API budget.** Hosted "deep research" runs cost dollars per run and gate
behind subscriptions; this is free on your own hardware, with re-checkable citations.
```bash
pw research "Most-cited 2025 papers on retrieval-augmented generation, and what each claims." --deep
```

**6. A researcher on modest hardware or metered/low bandwidth (e.g. the Global South).** Cap the model
size to fit the machine, and answer entirely from an already-downloaded corpus when bandwidth is scarce.
```bash
PW_MODEL_CAP_GB=3 pw research "Summarize current best practices for X" --quick   # fits a small box
pw research "Compare these PDFs I downloaded earlier" --local                    # zero live-web bandwidth
```

**7. A citizen on a time-sensitive question a frozen chatbot gets wrong.** Currency is the tool's real
edge — it leads with this year's sources instead of an SEO-dominant old page.
```bash
pw research "What changed in my country's energy-subsidy rules this month, and the deadlines?"
```

## Sovereignty, resilience & honest citations

**8. A regulated org needing data residency / technical sovereignty.** Data never leaves owned
infrastructure — sovereignty by construction, not by contract. Expose the engine to your *own*
approved agent with no egress:
```bash
pw library add /srv/compliance/docs
pw mcp     # serve research/library tools over MCP to your in-house assistant — nothing leaves the box
```

**9. Anyone burned by hallucinated citations.** The tool ships an honesty instrument that checks, for
every cited claim, whether the source actually says it (a grounding *floor*, never "verified true").
```bash
pw research "Draft a literature review on topic Y" --deep
python scripts/eval_citation_fidelity.py --report reports/<your-report>.md
```

**10. A developer wiring private deep research into their own agent.** One local capability your
assistant can call — composable, no lock-in, heavy lifting stays on your machine.
```jsonc
// claude_desktop_config.json
{ "mcpServers": { "passive-workers": { "command": "pw", "args": ["mcp"] } } }
```

## The commons — computers doing real work for each other (opt-in network)

> The network is the maturing next track. Today an operator joins an invite-only coordinator with an
> enrollment token; research-style jobs are submitted with `council/net/submit.py`, and the richer
> task types (batch, extract, code-gen, assisted) run through the coordinator API. It earns
> **non-transferable** mutual-aid credit — no token, no speculation.

**11. Cross-country deep research no single API can replicate.** Each node researches from its *own*
country's egress and returns its *own* cited findings (never proxied), merged with per-country
sections — a geo-diversity a centralized API structurally can't offer.
```bash
# operator (Berlin) lends a node:
export PW_COORDINATOR=https://<coord> PW_TOKEN=<tok> PW_OWNER=<acct> PW_COUNTRY=DE PW_WEB_BACKEND=ddgs
python -m council.net.agent
# asker submits a research job (same coordinator URL — set it in the asker's shell too):
export PW_COORDINATOR=https://<coord>
python -m council.net.submit --asker maria "How is <policy> reported and enforced across DE, BR, IN?"
```

**12. An idle home computer doing real, owned, mutual-aid work — BOINC / Folding@home for AI.** Dials
out only (no open ports), pauses under a CPU ceiling, stops anytime, and you always see the work class.
```bash
export PW_CAN_JUDGE=1 PW_JUDGE_MODEL=qwen3:14b
python -m council.net.agent
```

**13. An under-funded lab running a big batch across borrowed machines.** A large job is split across
computers (`shard_map`), each does a slice locally, and the parts are reassembled in order with
quality spot-checks — collective scale a small org can't buy. *(Submitted via the coordinator API.)*

**14. Splitting a public-URL extraction job across volunteers — without ever proxying.** With
`download_extract`, each node fetches a public page it could lawfully fetch alone and returns the
model's **extraction in its own words**, never the raw bytes and never a fetch-script (the D4 bright
line). Distributed throughput, owned deliverables only.

**15. Distributed code generation with a safe, human-in-the-loop integration step.** `code_generation`
produces one self-contained code unit per spec (**generation only — never executed**); the
"connect + build" step is routed through `assisted`, where a consenting human does it on their own
machine and delivers a signed, integrity-checked artifact:
```bash
pw tasks                       # an operator sees the offer + brief + price
pw accept <task_id>            # informed consent; prints the full brief
pw deliver <task_id> @build-artifact.zip <job_id>   # content-addressed, integrity-verified delivery
pw rate <job_id> 9             # the asker rates → builds operator reputation
```

---

## Why this matters
Most "AI research" today asks you to hand your questions, your documents, and your money to someone
else's cloud. Passive Workers is a bet that the opposite can be just as useful: your own models, your
own connection, your own disk — and, when you choose, a commons of computers helping each other.
That combination serves the people most under-served by the cloud model: those who can't pay, can't
risk the exposure, can't get the bandwidth, or simply believe their research should be their own.

*Sources for the real-world needs above (privilege/confidentiality rulings, the cost barrier to AI in
education and the Global South, journalist/surveillance pressure, GDPR/HIPAA data-residency, fabricated
citations in the literature, and volunteer-computing precedents) were gathered from public reporting
and primary sources during research for this document; see the project history for the full citation
list.*
