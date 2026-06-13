# Federation v2 — the multi-machine trust architecture (master structure)

> Status: **design**. This is the planned structure for turning the federation layer
> (`council/net/`) into a real multi-machine work marketplace. The security-critical pieces
> (2–4) ship only after a written security review. Built so far this round: the tamper-evident
> result digest (step 1). See `docs/DECISIONS.md` D18 for the governing principles.

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

### 0. Human-in-the-loop `assisted` task class — the centerpiece
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

Build phases: (a) job type + brief routing + a basic accept/decline operator view; (b) bounded-context
enforcement + per-class consent defaults; (c) licensed-software sub-type + capability gating.

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

### 3. Content-addressed artifact store + chunking — design
- Deliverables become **files**, not just text rows. Producer splits a file into chunks, hashes each,
  and commits a **Merkle root**; coordinator stores opaque (encrypted) blobs keyed by hash; asker
  reassembles and verifies against the root. Enables large + resumable + integrity-checked
  deliverables. The operator's "split files / share files" idea, done verifiably.

### 4. Consent + capability disclosure — design
- Nodes declare which work classes they accept: `research`, `batch` (low-risk, opt-in auto-approve)
  and gated classes `assisted`, `licensed-software`, `heavy-compute` (always-prompt).
- Builds on the existing capability profile (models/RAM, `store._meets`) — add work-class consent and
  the operator approval surface. No task type is ever offered to a node that didn't opt in.

### 5. MCP interop — roadmap
Ship an MCP server so Claude and other agents can call our research/marketplace engine, and let our
nodes call ecosystem tools via MCP. Makes us a node in the agent ecosystem, not a fork. (Prior art:
gpt-researcher's MCP server.)

## Threats this addresses
Hostile coordinator (steps 1–2), MITM (2), tampering node operator (1, + existing judge spot-checks &
reputation), prompt injection from web content (existing sanitizer/spotlighting), unauthorized use of
a host (transparency + consent, 0/4), and the legal exposure of automation/proxying (human-mediated
handoff, owned-deliverables — principles).
