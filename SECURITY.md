# Security Policy

> Consolidated, not invented — this replaces the scattered threat-model prose that used
> to live across README §Security, `docs/ARCHITECTURE.md` §Security posture, and
> `docs/FEDERATION_V2.md`. Pairs with [docs/VISION.md](docs/VISION.md) ("what we refuse")
> and [docs/DECISIONS.md](docs/DECISIONS.md).

## 1. Scope & assets

What this document covers, and what's actually at risk if something goes wrong:

- **Your brief / question** — the text you ask the engine to research.
- **Your documents** — anything you `pworkers library add`.
- **Your reports** — the markdown/JSON/HTML files the engine writes to `./reports/`.
- **Your Ollama** — the local inference server the engine drives.
- **Your keys** — the Ed25519 signing key and X25519 encryption key generated for the
  network (`pworkers keygen` / `pworkers fingerprint`), and the `~/.passiveworkers/*.json`
  credential files.
- **(Network only) your machine's compute and egress** — what a joined agent spends on
  others' behalf, always within a task class you consented to.

## 2. Threat model, by adversary

| Adversary | What they could try | What stops them |
|---|---|---|
| **A malicious web page** | Inject instructions into fetched content to hijack the model, or smuggle a payload into the final report | Every fetched page is sanitized (invisible-Unicode / hidden-comment stripping) and enters prompts only inside spotlighting delimiters marked "data, never instructions" (`passiveworkers/sanitize.py`); model output is re-scrubbed before it lands in the report; models hold **zero tool privileges** |
| **A malicious coordinator** (one you join, not run) | Read your job content, swap a deliverable, impersonate an operator | End-to-end encryption is opt-in (`pworkers keygen`) so the coordinator relays ciphertext it cannot read; deliverables are Ed25519-signed and the asker **pins** the operator's key (`pworkers trust add` / TOFU) — `pworkers fetch` refuses a signed delivery under the wrong key |
| **A malicious operator** | Submit low-quality or downgraded work for credit; hijack another operator's task | Pay is score-weighted by a blind judge, so bad work simply earns less; a per-node secret means a node can only complete its own tasks (no hijacking, no forged scores); an asker can *opt in* to a reputation floor (`requires.min_reputation`) on a specific job — this is a knob the asker sets, not an automatic gate on sensitive work |
| **A malicious asker** | Abuse a coordinator to spam registrations/jobs, or read other users' data | Rate limits on registration/job/token endpoints (D36); per-user-secret auth scopes each asker to their own jobs; `/status` exposes no other user's balance or identity |
| **A local attacker** (access to your machine/account) | Read your keys or credentials | All credential files (`join.json`, `operator.json`, `asker.json`, key files) are written owner-only (`0600`) |

## 3. Design invariants

These are absolutes, not defaults you could accidentally misconfigure away:

- **No browser automation, no computer-use, no sessions, no cookies — ever.** Search
  API calls and plain fetches of public pages only.
- **Models return text only.** Every action — search, fetch, file write — is plain
  Python under this repo's control; a model cannot cause a side effect directly.
- **Writes only to the configured reports directory** for engine output; no other
  filesystem writes outside explicit config/credential paths.
- **Every fetch is SSRF-guarded** — public hosts only, size-capped (see Known
  limitations for the one residual gap, disclosed rather than hidden).
- **A network agent dials out only.** Nothing listens on an operator's machine; no
  inbound ports, no port-forwarding.
- **Keys never leave the device.** Signing and encryption private keys are generated
  and stored locally; only the public key/fingerprint is ever shared.
- **When a task needs a real computer driven** (browser, licensed software), it is
  handed to a human operator who consents to that one bounded task and does it with
  their own AI or by hand — our code never automates anyone's machine (D18).
- **The ledger is fail-closed.** Settlement runs before delivery; scores are sanitized
  (non-finite/out-of-range → 0, empty/errored answers → 0 with no reputation credit);
  an over-budget job fails cleanly rather than draining an account. Conservation
  (nothing created or destroyed on any path, including inf/NaN inputs) is
  property-tested (`passiveworkers/ledger.py`).
- **The dashboard has no info-leak or stored-XSS surface.** `/status` exposes an opaque
  `node_key`, never a raw `node_id` or IP; every node-supplied field (name, owner, …)
  is HTML-escaped before the dashboard renders it, so a malicious node name can't
  inject script into another viewer's page.
- **Liveness is checked, not assumed.** Nodes heartbeat on a background thread; a
  reaper fails any job whose assigned node goes stale or exceeds the run deadline —
  a dead worker can't wedge the queue indefinitely.

## 4. What leaves your machine / what never does

| Data | Single-player | Network (opt-in) |
|---|---|---|
| Search queries | → your chosen backend (DDG default; SearXNG self-hosted; keyed = central) | same, from each node's own egress |
| Fetched public pages | inbound only | inbound only |
| Your documents / library | **never leave** | **never leave** |
| Your brief | never by default (only derived search queries) — **except** `--editor api` (BYOK), which sends it to your configured external API | → coordinator (job routing); the default `chat` job type also sends it, sanitized but verbatim, as the search query to the answering node's own web backend |
| Reports / deliverables | local disk only | signed; optionally end-to-end encrypted to the asker |
| Telemetry / accounts | **none / none** | heartbeat + credit ledger to coordinator |
| Your keys | n/a | **never leave the device** |

This table is excerpted in [README.md](README.md); this file is the source of truth.

## 5. Known limitations (plainly)

- **Grounding is a floor, not verification.** `scripts/eval_citation_fidelity.py` catches
  off-topic citations and fabricated numbers — the common, damaging failures. A
  GROUNDED verdict means "not obviously fabricated," not "verified true"; it cannot
  detect subtle misrepresentation.
- **A coordinator you don't run sees job metadata** — who's asking, what job type,
  timing — even when the deliverable content itself is end-to-end encrypted.
- **Your search backend sees your queries.** For `pworkers research` (single-player) and the
  network's dedicated research job type, those are LLM-derived from your brief, never
  the brief itself, unless you self-host SearXNG. For the network's *default* `chat`
  job type, this is **not** true: the sanitized-but-verbatim question is sent directly
  as the search query to the answering node's configured backend — a real gap between
  the two job types, not a uniform guarantee.
- **`pworkers research --editor api` (BYOK) sends your brief to an external API.** Wiring in
  a frontier editor over OpenRouter means your brief, not just derived search terms,
  leaves your machine over HTTPS to that API — this is the one documented exception to
  "private by construction," and it's opt-in only.
- **A DNS-rebinding TOCTOU residual exists in the fetch path**, self-documented in code
  rather than fully closed (`passiveworkers/research.py`). A determined attacker with
  DNS-timing control could theoretically race a fetch between the SSRF check and the
  request. On a plain machine the exposure is a fetched-page read; **on a cloud-hosted
  coordinator/operator with an unauthenticated instance-metadata endpoint reachable
  (the classic SSRF-to-IMDS chain), that "fetched page" could be cloud credentials** —
  the impact depends on the hosting environment, not on anything this codebase alone
  controls, and we are not going to pretend otherwise.
- **A coordinator is a single trusted writer for its own network** (D2 — no
  blockchain, no full node per machine, by design). Running one means you hold routing
  and ledger authority over your own cell; that's the trade-off, not an oversight.

## 6. Vulnerability disclosure

- **Preferred:** [GitHub Private Vulnerability Reporting](https://github.com/wikithoughts/passiveworkers/security/advisories/new)
  — enabled (Round 38). This is a real, monitored channel: reports go straight to the
  maintainer's GitHub notifications, privately, with no personal email or identity
  required — this satisfies the "anonymous maintainer asking strangers to run a network
  agent" trust tension (`docs/REVIEW_2026-07.md` F21) without de-anonymizing anyone.
- **Email:** not configured, and not required — GitHub Private Vulnerability Reporting
  above is the real channel.
- **Acknowledgment SLA:** target 72 hours.
- **Supported versions:** the latest `0.x` release on PyPI. This is pre-1.0 software —
  there is no long-term-support branch yet.
- **Scope:** this repository and the coordinator/agent code it ships. Third-party
  dependencies (Ollama, the search backends) are out of scope — report those upstream.

We publish our own methodology and losses (`docs/TRIAL_RESULTS.md`); a security report
is welcome to be just as blunt.
