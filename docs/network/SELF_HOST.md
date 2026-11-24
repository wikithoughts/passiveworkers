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
tokens for others. Losing it means regenerating and re-inviting everyone. There is no default token
(D56): if `PW_TOKEN` is **unset**, the coordinator generates a random one on first start and saves
it, mode `0600`, at `~/.passiveworkers/admin_token` (`$PW_HOME/admin_token`). It is never printed or
logged; the startup line names the file, so read it with `cat ~/.passiveworkers/admin_token` and use
it as `PW_TOKEN` in the `pworkers invite` commands below. The file is reused on every restart.

Safety rules for that file (the coordinator enforces them at startup):
- Generation, rotation and repair are serialised with an `flock` on `$PW_HOME/.admin_token.lock`, so
  several coordinators starting together all end up with the same token. Without `flock` support the
  coordinator warns and falls back to unserialised behaviour: start them one at a time.
- A token file with any group/other permission bit is treated as compromised and **rotated**, not
  adopted (the startup line says `rotated (was mode 0xxx)`; update clients with the new token).
  Exception: on a filesystem that ignores `chmod` (vfat, some SMB shares or Docker volumes) the bits
  can never be cleared, so the coordinator warns "filesystem does not enforce permissions; token not
  rotated" and keeps the token. Put `PW_HOME` on a real POSIX filesystem to get the protection.
- `PW_HOME` must be owned by the coordinator's user and not world-writable. A group-writable `PW_HOME`
  (mode 0775, the Debian/Ubuntu default umask) is accepted only when the group is your own private
  group, with a warning (`chmod g-w` silences it); any other group-writable or foreign-owned directory
  is refused with an error naming `PW_HOME`.

**Upgrading from a build that defaulted to `dev-token`:** `dev-token` is no longer accepted, on
loopback or anywhere else. If `PW_TOKEN` is unset or `dev-token`, the coordinator now uses the
generated token and says so at startup; update any script, `pworkers invite` call or node agent that
sent `dev-token` to use that token (or set your own `PW_TOKEN`). An explicitly **empty** `PW_TOKEN=`
still disables the admin routes (they answer 401). The coordinator warns at startup about a token
that is empty or shorter than 24 characters, even on loopback: a tunnel or reverse proxy makes
loopback public.

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
  wrong, it lets a client spoof its way past your limits. Behind a tunnel or proxy on a
  known address, prefer `PW_TRUSTED_PROXIES=127.0.0.1,::1` (comma-separated IPs/CIDRs): the
  `X-Forwarded-For` header is then honoured only when the connection comes from one of those
  addresses, and the client is the right-most hop that is not a trusted proxy — without it,
  every client behind a loopback tunnel shares one global pre-auth rate-limit bucket.
- **Back up `PW_DB`** (the SQLite file holding the ledger, accounts, and reputation).
  The store migrates its own schema on boot (`ALTER TABLE`), so upgrading in place is
  safe — losing the file is not recoverable.
