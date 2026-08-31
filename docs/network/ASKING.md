# Asking the network for work

You have a coordinator URL and (usually) an enrollment token from whoever runs it —
either the maintainer, or a friend who [self-hosts](SELF_HOST.md). This page is the
asker-side flow: submit a job, fetch a file deliverable, verify it, rate it.

## 1. Get an identity and submit

```bash
export PW_COORDINATOR=https://<coordinator-url>
pworkers ask "What changed in EU AI Act enforcement this quarter?" --enroll-token <token>
```
On first run, `pworkers ask` signs up a new asker identity, **prints the minted secret, and
saves it** to `~/.passiveworkers/asker.json` (owner-only, `0600`) — so unlike the raw
API, the secret can't evaporate when the process exits. Every later `pworkers ask`, `pw
fetch`, and `pworkers rate` against the same `PW_COORDINATOR` reuses it automatically; no
need to re-export a secret or pass `--enroll-token` again.

Useful flags:
- `--type <job-type>` — request a specific job type (`GET /job-types` on the
  coordinator lists what it supports; default is a standard research job).
- `--asker <handle>` — pick your display handle on first signup (defaults to
  `alice` if you don't set one — change it, it's just a placeholder default).
- `--timeout <seconds>` — how long to poll before giving up (default 600).

`pworkers ask` prints each node's answer with its score, then the merged answer and the
credit ledger movement for the job.

## 2. Fetch a file deliverable

Some jobs (assisted tasks especially) deliver a real file, not just text:
```bash
pworkers fetch <job-id> ./downloads
```
This verifies every chunk (a corrupted or swapped one is detected, never written) and
reassembles the original file. If the job required end-to-end encryption
(`encrypt_to` was set when you asked) and the deliverable somehow arrives
unencrypted, `pworkers fetch` **refuses it** rather than silently accepting a downgrade.

## 3. Verify who actually did the work (signed deliverables)

With the `[crypto]` extra installed, deliverables are Ed25519-signed. `pworkers fetch` shows
whether the signature is valid, and pins the operator's key on first contact
(trust-on-first-use). To pin a key out of band instead of trusting first contact, get
the operator's actual **public key** — not the short fingerprint — from them (they run
`pworkers fingerprint`, which prints both the full key and a short fingerprint "read this to
them over a trusted channel" to visually confirm it's the right one):
```bash
pworkers trust list                       # operators you've pinned
pworkers trust add <operator> <pubkey>    # the full key from their `pworkers fingerprint`, not the short fingerprint string
pworkers trust remove <operator>
```
Once pinned, a delivery signed by a *different* key for that same operator name is
rejected — protects against a hostile coordinator swapping in an impostor.

Note for anyone watching a coordinator's live map (`/dashboard`): an operator who has
both `pworkers join`ed to contribute research jobs *and* accepts assisted tasks via `pw
tasks`/`pworkers accept`/`pworkers deliver` now shows up as one node, not two — the assisted flow
reuses the same `pworkers join` node identity on that machine instead of registering a
second one. This is purely cosmetic; it never changes credit or reputation, which have
always been tracked per-owner rather than per-node.

## 4. Rate the result

```bash
pworkers rate <job-id> 8   # 0-10, feeds the operator's reputation
```
Reputation is a rolling average of judge scores plus asker ratings; it gates
operators into higher-trust work over time.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `✗ no asker identity found` | Run `pworkers ask "..." --enroll-token <token>` at least once first — `fetch`/`rate` need the identity it persists. |
| `409` on signup / handle taken | `pworkers ask` auto-retries with a randomized suffix — if you still see this, the coordinator itself may be unreachable; check `PW_COORDINATOR`. |
| `✗ ... required encryption but the deliverable is not encrypted` | This is `pworkers fetch` correctly refusing a downgrade — do not treat it as a bug to route around; the deliverable is not trustworthy. |
| Pinned-but-unsigned delivery | If you previously pinned an operator's key and a later delivery isn't signed at all, `pworkers fetch` rejects it — the operator's setup likely lost its signing key or the `[crypto]` extra. |
| `insufficient credit` | Your asker balance is too low for the job's cost — a starter grant comes with most enrollment tokens (`pworkers invite --grant N` on the operator side controls this). |

See [SELF_HOST.md](SELF_HOST.md) if you'd rather run the coordinator yourself instead
of joining someone else's, and [CONTRIBUTE_COMPUTE.md](../CONTRIBUTE_COMPUTE.md) for
the other side of this — contributing your own machine's idle time.
