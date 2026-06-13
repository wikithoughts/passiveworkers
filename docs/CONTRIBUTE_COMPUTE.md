# Contribute your compute

Passive Workers has a second mode beyond the single-player `pw research` tool: an opt-in
**federation** where your computer's idle time does real, owned work and earns non-transferable
credit. This page is for people who want to plug a machine in.

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
  drive a browser, click, or touch your sessions/cookies — by design, permanently (D16/D18).
- **You stay in control.** Stop the agent any time (Ctrl-C / stop the service). A CPU-ceiling
  pause gate keeps it from hogging the machine.
- **Untrusted content is treated as data.** Web pages and job inputs are sanitized and
  "spotlighted" (data, never instructions); the models hold zero tool privileges.
- **Cryptographic delivery.** Deliverables are signed; files are content-addressed and can be
  end-to-end encrypted to the asker. See D23/D25.

## Join a coordinator

You'll receive two things from the maintainer: a **coordinator URL** and a **token**.

```bash
# 1. Install (Python 3.12+ and a local Ollama with at least one model)
pip install passiveworkers          # or: pip install -e '.[all]' from a clone
ollama pull qwen2.5:14b             # any chat model works; bigger = better answers

# 2. Point the agent at the coordinator and start it
export PW_COORDINATOR="https://<coordinator-url>"
export PW_TOKEN="<your-token>"
export PW_OWNER="<the account that earns your credit>"
export PW_ANSWER_MODEL="qwen2.5:14b"      # the model you contribute
export PW_COUNTRY="DE"                      # optional: your locale (geo-diversity is the moat)
export PW_CAN_JUDGE=0                       # set 1 (+ PW_JUDGE_MODEL) to also judge
export PW_WEB_BACKEND=ddgs                  # optional: enable live web research from your egress

python -m council.net.agent
```

It registers, heartbeats, and starts taking jobs. Leave it running; stop it whenever you like.

- **Always-on (Linux):** `scripts/install_systemd.sh` installs a systemd unit that survives reboot.
- **Mac trial:** `scripts/mac_join.sh` opens a tunnel and runs a couple of perspectives + a judge.

## The geo-diversity moat

When your agent does web research, it searches from **your** internet egress — so a node in Berlin
sees different sources than one in São Paulo. That diversity is something no central API can
replicate, and it's why where your machine *is* matters. Contributing from an under-represented
region is especially valuable.

Questions, or want a coordinator URL + token? Open an issue or reach the maintainer.
