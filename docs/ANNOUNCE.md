# Announcement drafts (ready to post — maintainer posts these)

These are drafts for you to publish when you choose. Nothing here has been posted. Adjust the
voice, fill the bracketed links, and ship. Lead with the single-player product (it's the adoption
engine — what brings people in), but frame it as the **flagship task of a bigger idea**: make your
computer work for you and others — a network where computers do real work for each other. The
network is the other half of the project, not a throwaway P.S.

---

## Show HN draft

**Show HN: Passive Workers – local deep-research engine (your models + the live web, cited)**

I built a local-first deep-research tool. You point it at a question; several of *your own* Ollama
models research the live web as independent analysts (each from a different angle), and a blind
editor compiles one cited markdown report that preserves their disagreements instead of flattening
them into a false consensus.

Why it might interest you:

- **Private by construction.** No account, no server, no telemetry. The only thing that leaves your
  machine is the web searches; reports are files on your disk.
- **Plural by design.** Different model families make different mistakes. Question-diversity ×
  model-diversity catches what any single model hallucinates, and the report shows where the
  analysts agreed, differed, and what only one of them found.
- **It reads your own files too.** Local-documents RAG (hybrid BM25 + dense, contextual chunks) so a
  report can blend your notes (`[L#]`) with the live web (`[S#]`).
- **Callable as an MCP tool**, so your own agentic AI can use it as a capability.
- **Honest about itself.** It ships two eval instruments: one checks whether every cited claim
  actually appears in its source (citation fidelity), and one measures where live-web research beats
  a frontier model's frozen training knowledge (the currency gap). It tells you where a frontier
  chatbot is the better tool.

It's young software, labeled honestly: the single-player engine works and is verified end-to-end;
the optional federation below is newer.

```
pip install 'passiveworkers[all]'
pw research "your question"
```

Repo: [github.com/wikithoughts/passiveworkers](https://github.com/wikithoughts/passiveworkers)

The other half — the **network**: this is *why it's called Passive Workers*. Opt in and your
computer does owned jobs for other people's computers (and theirs for yours), earning
non-transferable credit — no token, no proxying anyone's traffic; your machine does the work and
returns what it produced. Research is the first job type; the network is built to split a job
across machines and reassemble it. Early access; see docs/CONTRIBUTE_COMPUTE.md.

---

## Short social version (≤280 chars)

Passive Workers: a local deep-research engine. Your own Ollama models research the live web as
independent analysts; a blind editor writes one cited report that keeps their disagreements. Private,
keyless, free. `pip install 'passiveworkers[all]' && pw research "…"` → [link]

---

## One-liner

A local-first deep-research engine: multiple models you already run, cross-checking each other over
the live web, into one cited report — private, keyless, and honest about where a frontier model wins.
