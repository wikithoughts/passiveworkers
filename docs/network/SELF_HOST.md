# Self-host a coordinator

> **Honest interim note:** there is no `pworkers host` one-command wrapper yet. This
> documents the real, working recipe — running `passiveworkers/net/coordinator_app.py`
> directly, the same thing `scripts/vps_run.sh` and `docker-compose.yml`'s
> `coordinator` service already do. When `pworkers host` ships, this page gets simpler, not
> different.

## Why self-host

You don't need the maintainer's invite to run a network. Two friends, two machines,
one coordinator either of you runs — that's a real commons already: one asks, the
other's idle machine helps, credit tracks who owes whom. You are the trusted party for
your own cell (see D2 in [DECISIONS.md](../DECISIONS.md) — one coordinator, not a
blockchain, by design); that's the trade-off, not a limitation to work around.

## Prerequisites

- Python 3.10+, and a machine that can stay reachable (a VPS is easiest; a home machine
  behind a reverse-proxy tunnel works too).
- `openssl` (to generate a token) — every OS ships one.

## 1. Get the code and generate a token

```bash
git clone https://github.com/wikithoughts/passiveworkers && cd passiveworkers
pip install -e '.[all]'
export PW_TOKEN=$(openssl rand -hex 24)   # this is your ADMIN token — keep it secret
```
`PW_TOKEN` gates every write endpoint on the coordinator, including minting enrollment
tokens for others. Losing it means regenerating and re-inviting everyone.

## 2. Run it — loopback first

```bash
PW_TOKEN=$PW_TOKEN PW_ENROLL=1 python -m passiveworkers.net.coordinator_app
```
`PW_ENROLL=1` is what actually makes step 3's enrollment tokens required — without it,
registration falls back to accepting the raw admin `PW_TOKEN` for nodes, and `/users`
signup is wide open to anyone with no token at all (fine for a quick local test with
someone you trust completely; wrong for anything you're calling "invite-only"). By
default this binds `127.0.0.1:8088` — reachable only from this machine. Confirm:
```bash
curl -sf http://127.0.0.1:8088/healthz && echo OK
```
**Never bind `PW_HOST=0.0.0.0` without TLS in front of it.** The coordinator's own
startup guard refuses to bind non-loopback with a weak/default token, but it does not
add TLS for you — put a reverse proxy (Caddy, nginx, or a Cloudflare Tunnel) in front
before you expose it to the internet. `docker-compose.yml`'s `coordinator` service and
`scripts/install_systemd.sh` (always-on, survives reboot) both follow this same
loopback-first pattern.

## 3. Mint an enrollment token

With `PW_ENROLL=1` set, registration requires a per-operator or per-asker enrollment
token — the shared `PW_TOKEN` above is the *admin* token, used only to mint these, not
something you hand out. Mint one:
```bash
PW_COORDINATOR=http://127.0.0.1:8088 PW_TOKEN=$PW_TOKEN \
  pworkers invite --kind any --max-uses 5
```
This prints a token plus the exact commands to hand to whoever you're inviting. This
is safe to run against your own coordinator — you already control it.

## 4. Your friend joins

They run, on their own machine:
```bash
pworkers join http://<your-coordinator-host>:8088 <the-enrollment-token> \
  --owner <their-handle> --model qwen3:14b --country <their-ISO-code>
```
(Full flag reference: [CONTRIBUTE_COMPUTE.md](../CONTRIBUTE_COMPUTE.md).) `pworkers join`
persists their identity so `pworkers work` resumes later with no token needed.

## 5. Optionally, run an operator on the coordinator host too

There's nothing stopping the coordinator's own machine from also contributing compute
— `pworkers join http://127.0.0.1:8088 <token> ...` on the same box, exactly like the other
operator. `scripts/vps_run.sh`/`install_systemd.sh` do this by default (a coordinator
+ one local worker together).

## 6. Point askers at it

Once at least one operator is online, an asker submits work with `pworkers ask` — see
[ASKING.md](ASKING.md) for the full flow, using the coordinator URL from step 2 and an
enrollment token minted the same way as step 3 (`--kind user` instead of `any`/`node`).

## Your responsibilities as the coordinator operator

- **You are the trusted party.** You see job metadata (who's asking, what job type,
  timing) even for end-to-end-encrypted deliverables — see
  [SECURITY.md](../../SECURITY.md) §4-5.
- **Rate limits are on by default** (D36) — tunable via `PW_RL_*` env vars. If you put
  more than a handful of operators behind a reverse proxy, read the `PW_TRUST_XFF`
  warning in [CONTRIBUTE_COMPUTE.md](../CONTRIBUTE_COMPUTE.md) before setting it —
  wrong, it lets a client spoof its way past your limits.
- **Back up `PW_DB`** (the SQLite file holding the ledger, accounts, and reputation).
  The store migrates its own schema on boot (`ALTER TABLE`), so upgrading in place is
  safe — losing the file is not recoverable.
