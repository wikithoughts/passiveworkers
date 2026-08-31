# Contribute your compute

The single-player `pw research` tool is one half of Passive Workers. This is the other half — and
the reason for the name: an opt-in **network** where your computer's idle time does real, owned
work for other people's computers (and theirs for yours) and earns non-transferable credit.
Research is the first job type the network runs; more are on the roadmap. This page is for people
who want to plug a machine in.

> Status: **early access.** The coordinator is invite-only today — there is no open public
> endpoint yet. You join a specific coordinator with a URL + token the maintainer gives you.
> The software is young but the trust machinery below is built and tested.

## What your machine actually does

Your machine runs a small **agent** (`council/net/agent.py`) that:

1. **Dials out** to a coordinator (HTTP). It never accepts inbound connections — nothing listens
   on your machine, no ports are opened.
2. **Polls for jobs** and runs them on **your own local Ollama** — answering a question from a
   distinct perspective, judging others' answers, a slice of a batch, or deep web research from
   *your* internet egress.
3. **Returns an owned deliverable** — a result your machine produced. 

**The bright line: you never proxy anyone else's traffic.** No residential-proxy exit, no VPN
tunnel, no relaying packets. Your agent does the work and hands back the output it made. That is
ordinary, defensible work — and it is the single most important design rule of the whole project
(see [DECISIONS.md](DECISIONS.md) D4).

## What you get

- **Non-transferable credit** for work your machine completes, scaled by quality (a blind judge
  scores answers; good work earns more). Credit is internal and earned-only — there is no token,
  no speculation, no secondary market (D1). Cash-out, when it exists, happens only at the platform
  edge.
- **Reputation** that compounds: your average judge score gates you into higher-trust work over
  time, and askers can pin your signing key so they verify *your* deliverables specifically.

## Why it's safe to run

- **No inbound, no open ports.** The agent only dials out.
- **No computer-use, ever.** The agent runs model inference and returns text/files. It does not
  drive a browser, click, or touch your sessions/cookies — by design, permanently (D18).
- **You choose the kinds of work your machine accepts when you join** (research, judging, batch,
  assisted) — every task it runs is visible in the log, and you can stop it at any time (Ctrl-C /
  stop the service; a CPU-ceiling pause gate also keeps it from hogging the machine). Sensitive
  work (anything touching a real computer via `assisted`) is never auto-run; a human always sees
  the brief and consents to that one task (see D53 in [DECISIONS.md](DECISIONS.md)).
- **Untrusted content is treated as data.** Web pages and job inputs are sanitized and
  "spotlighted" (data, never instructions); the models hold zero tool privileges.
- **Cryptographic delivery.** Deliverables are signed; files are content-addressed and can be
  end-to-end encrypted to the asker. See D23/D25.

## Join a coordinator

You'll receive two things from the maintainer: a **coordinator URL** and an **enrollment token**.
Then it's **one command**:

```bash
# 1. Install (Python 3.10+ and a local Ollama with at least one model)
pip install passiveworkers          # or: pip install -e '.[all]' from a clone
ollama pull qwen2.5:14b             # any chat model works; bigger = better answers

# 2. Join — registers, persists your identity, and starts taking jobs
pw join https://<coordinator-url> <enrollment-token> \
   --owner <account-that-earns-your-credit> --model qwen2.5:14b --country DE
```

`pw join` saves your config + node identity to `~/.passiveworkers/join.json` (owner-only `0o600`),
so next time you just run **`pw work`** (or `pw join` with no args) to resume — no token needed.
Judging is **on by default** (a lone contributor's node must be able to both answer and judge, or
a small deployment fails every job) — pass `--no-judge` if you only want to answer, not judge
others'. Other optional flags: `--lens`, `--judge-model`, `--web off` (web research is on by
default so your node can take research jobs). Leave it running; stop it any time (Ctrl-C).

- **Always-on (Linux):** `scripts/install_systemd.sh` installs a systemd unit that survives reboot.
- **Mac trial:** `scripts/mac_join.sh` opens a tunnel and runs a couple of perspectives + a judge.

If this same machine also does assisted (human-in-the-loop) work for the same coordinator via
`pw tasks` / `pw accept` / `pw deliver`, it reuses the identity already saved in `join.json`
instead of minting a second one — so you show up once on the coordinator's live node map, not
twice. This only changes which identity gets picked for the assisted flow; if you've never run
`pw join` against a coordinator, `pw tasks`/`pw accept`/`pw deliver` still register and cache their
own identity in `~/.passiveworkers/operator.json` exactly as before.

<details><summary>Advanced: the explicit env-var flow (what <code>pw join</code> automates)</summary>

```bash
export PW_COORDINATOR="https://<coordinator-url>"
export PW_ENROLL_TOKEN="<enrollment-token>"   # or PW_TOKEN for a non-enrollment coordinator
export PW_OWNER="<the account that earns your credit>"
export PW_ANSWER_MODEL="qwen2.5:14b"
export PW_COUNTRY="DE"                          # geo-diversity is the moat
export PW_CAN_JUDGE=0                           # set 1 (+ PW_JUDGE_MODEL) to also judge
export PW_WEB_BACKEND=ddgs                      # live web research from your egress
python -m council.net.agent
```
</details>

## For coordinator operators: rate limits (D36)

The coordinator rate-limits its creation/mint endpoints (`/nodes/register`, `/users`, `/jobs`,
`/tasks/*/progress`) — tunable via `PW_RL_*` env vars (per 60s; set `0` to disable one). By default
the limit key is the **socket peer**, which behind a tunnel is loopback — i.e. a conservative
**global** cap that is spoof-proof. Running more than a handful of operators? Put the coordinator
behind a reverse proxy that **sets and sanitizes** `X-Forwarded-For`, then set `PW_TRUST_XFF=1` for
per-client limits. Do **not** set `PW_TRUST_XFF` without such a proxy — clients could rotate the
header to bypass the limits. Rate limits bound abuse *rate*; gating *who* may register at all is a
separate, already-shipped mechanism (D37): with `PW_ENROLL` on, `/nodes/register` requires a valid
`X-Enroll-Token` minted by an operator via `/admin/enroll`, and `/users` (asker signup) stays open
but grants **zero** starter credit without one — so identity creation stays frictionless while free
credit stays gated.

## The geo-diversity moat

When your agent does web research, it searches from **your** internet egress — so a node in Berlin
sees different sources than one in São Paulo. That diversity is something no central API can
replicate, and it's why where your machine *is* matters. Contributing from an under-represented
region is especially valuable.

Want to run your own coordinator instead of joining someone else's? See
[docs/network/SELF_HOST.md](network/SELF_HOST.md). Want to *ask* the network for work
rather than contribute compute? See [docs/network/ASKING.md](network/ASKING.md).

Want a coordinator URL + token from the maintainer instead of self-hosting? Use the
[join-the-network waitlist](https://github.com/wikithoughts/passiveworkers/issues/new?template=join-the-network.yml).
Other questions → open an issue.
