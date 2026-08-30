# Federation v2 — the multi-machine trust architecture (master structure)

> Status: steps 0–3 are **built**, step 4 is **partially built**, and §5 (MCP interop) is
> **built** — this doc originally described all of it as future design; it now records what
> actually shipped, with the D-numbers and rounds. See `docs/DECISIONS.md` D18 for the
> governing principles.

## Governing principles (non-negotiable)

- **Informed, tiered consent (never deception).** An operator gives informed consent to a *class*
  of work; individual tasks in an opted-in class then run without a per-task prompt (the
  BOINC/SETI@home pattern), while they always know the class, can audit logs, and can stop.
  Sensitive classes escalate to explicit per-task approval with a brief + minimal context. Only
  deception is forbidden — running work whose nature the operator was never told. (D4, D18.)
- **Owned deliverables, never proxied traffic.** Nodes return work they produced; they never relay
  raw bytes or route another user's requests through their IP. (D4, D15.)
- **Computer-use is human-mediated, never autonomous agent code.** When a task needs a real
  computer driven (browser, licensed software), it is handed to the human operator, who does it with
  their own agentic AI or by hand, under approval. Our code never automates anyone's machine. (D18.)
- **Money only at the edges; non-tradeable credit.** (D1.)

## The components

### 0. Human-in-the-loop `assisted` task class — BUILT (D21, R9) — the centerpiece
The marketplace capability that makes "computer-use" safe and legal.

- New job type `assisted`. The asker submits a brief + the **minimal** context needed.
- Coordinator routes it to a consenting, capable operator (capability match, step 4).
- **Operator approval UI**: the operator sees exactly what is asked, the bounded context, and the
  price, then accepts or declines. Nothing runs without this.
- The operator completes the work — via their own agentic AI (Claude, Codex, …) or by hand — and
  returns the **owned deliverable**.
- Deliverable is verified (steps 1–3) and settled (existing score-weighted ledger).
- **Licensed-software tasks** are a sub-type: the brief declares "needs your licensed X"; only
  operators who advertised that capability and opted in are offered it; always-prompt.

Build phases: (a) job type + brief routing + a basic accept/decline operator view — **done**; (b)
bounded-context enforcement + per-class consent defaults — **done**; (c) licensed-software sub-type +
explicit capability-declaration flags (`licensed-software`, `heavy-compute`) at node registration —
**not built** (verified: no such flags exist in `council/`'s registration code). See D21.

### 1. Tamper-evident results — BUILT
Every task result is stored with a canonical SHA-256 (`store.result_digest`), surfaced in
`job_view` per answer. Any later alteration of a stored deliverable is detectable. This is the floor;
step 2 raises it to defend against a hostile coordinator.

### 2. Signed + optionally encrypted delivery — BUILT (D23) + out-of-band key trust (D25)
- Each node holds a keypair; deliverables are **signed** with the node's private key and verified by
  the asker against the operator's signing key (`council/crypto.py`, `[crypto]` extra).
- Optional **end-to-end payload encryption**: the asker publishes a public key with the job; the
  producer encrypts the deliverable to it; the coordinator relays opaque ciphertext it cannot read.
  Real confidentiality even against a hostile coordinator.
- **Out-of-band key trust (D25, BUILT):** the asker pins an operator's signing key locally
  (`council/trust.py`; TOFU on first signed delivery, or explicit `pw trust add` after comparing a
  `pw fingerprint`) and `pw fetch` verifies against the **pinned** key — so signing now defeats even
  a fully hostile coordinator for any pinned operator (it can't present a different key or forge a
  signature under the pinned one). This was the open caveat the original step-2 design flagged.
- This is the operator's "encryption between computers / no one can inject into the work," at the
  right layer.

### 3. Content-addressed artifact store + chunking — BUILT (D22, R10)
Deliverables are **files**, not just text rows. `council/artifacts.py` (stdlib only) splits a file
into 256 KiB chunks, sha256-hashes each (the chunk's own address), and records a manifest of
`{name, size, chunk_size, root, chunks:[hashes]}`, where `root` is a flat **Merkle root** (sha256 of
the ordered chunk hashes). The coordinator stores chunks as opaque, deduped, content-addressed blobs;
the asker's receiver verifies every chunk against its hash and the manifest root before reassembling,
and rejects on any mismatch, missing chunk, or path-traversal attempt. Operator side: `pw deliver
<task> @file <job>`; asker side: `pw fetch <job> <dir>`. See D22 for auth, caps, and the adversarial
fixes (dedup-per-job PK, blob-presence check before settlement, retention reaper).

### 4. Consent + capability disclosure — PARTIALLY BUILT
- **Consent (D53, settled):** operators give class-level consent at `pw join` (which work classes —
  research/judging/batch/assisted — their node accepts); `assisted` tasks additionally require
  explicit per-task consent via `pw accept`, since that's the sensitive, human-mediated class (D18).
- **Capability gating (D24, settled):** marketplace offers can require a capability/reputation
  threshold (`requires.min_reputation`, model/RAM match); enforced at both offer-listing and accept,
  fail-closed on a malformed threshold (`store._meets`, `store._meets_reputation` in
  `council/net/store.py`).
- **Still open:** explicit `licensed-software`/`heavy-compute` capability-*declaration* flags at node
  registration (same gap as §0 phase c) — nodes aren't yet able to advertise these specific
  capabilities for gating to key off.

### 5. MCP interop — BUILT (D19, R7)
`pw mcp` (optional `[mcp]` extra) runs an MCP server (`council/mcp_server.py`, stdio) so Claude
Desktop, Codex, or any MCP client can call the research/marketplace engine as a tool. Exposes 3 tools:
`research`, `library_search`, `library_add`. Makes us a node in the agent ecosystem, not a fork.
(Prior art: gpt-researcher's MCP server.) See D19 for the adversarial-review fixes (path confinement,
ingest caps, sanitized output, stdout-corruption fix).

## Threats this addresses
Hostile coordinator (steps 1–2), MITM (2), tampering node operator (1, + existing judge spot-checks &
reputation), prompt injection from web content (existing sanitizer/spotlighting), unauthorized use of
a host (transparency + consent, 0/4), and the legal exposure of automation/proxying (human-mediated
handoff, owned-deliverables — principles).
