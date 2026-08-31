# Passive Workers — instructions for AI coding agents

Three lines: this is a local deep-research engine (independent local-model analysts +
a blind editor writing one cited report), plus an opt-in commons where idle machines
do research for each other for non-transferable credit — no token, no cloud in the
middle. If you're only discussing the project, [docs/VISION.md](docs/VISION.md) is a
better starting read. This file is for when you're about to change the code.

## Source of truth, in order

1. **[docs/VISION.md](docs/VISION.md)** — positioning: what this is and isn't. Don't
   restate it differently in a PR description or a docstring; link it instead.
2. **[docs/DECISIONS.md](docs/DECISIONS.md)** — every settled architectural choice,
   ADR-style, with the rejected alternatives and why. **Do not re-propose or silently
   relitigate D1 (no token, no tradeable credit), D4 (nodes return owned work, never
   proxied traffic), or D18 (no browser automation / computer-use, ever, permanently)
   without an explicit founder decision.** These three are load-bearing to the whole
   trust model, not ordinary technical choices.
3. **[docs/ROADMAP.md](docs/ROADMAP.md)** — what's shipped, round by round, and what's
   next. Check here before assuming a gap is unaddressed.
4. **[SECURITY.md](SECURITY.md)** — the threat model. If your change touches request
   handling, credential storage, or fetched content, read this first.

## Never weaken these without a very good reason, stated explicitly

- `passiveworkers/sanitize.py` — every fetched page and every model-written passage is
  sanitized and spotlighted ("data, never instructions") before it can reach a prompt
  or a report. This is the one thing standing between "web content" and "prompt
  injection." Extending it needs new tests; narrowing it needs a DECISIONS.md entry.
- `passiveworkers/net/coordinator_app.py`'s startup guard — refuses to bind a public
  interface with a weak/empty token. Don't add a bypass flag "for testing."
- Any `0600`-mode credential write (`passiveworkers/paths.py:write_private_json`,
  `passiveworkers/config.py`'s `_save`, `join.json`/`asker.json`/`operator.json`) — these
  hold signing keys and node/asker secrets. A write path that skips the owner-only
  permission window is a real vulnerability, not a style nit.
- The **give/take rule** and **no proxied traffic** invariants (D1/D4 above) — the
  entire legal and trust posture of the network depends on both holding exactly as
  documented.

## Vocabulary note

Round-32-onward code and docs use: analyst / blind judge / editor / research desk
(single-player), and coordinator / operator / asker / assisted task / credit
(network). Some older files still say "The Council," "Substrate," "Perspective," or
"Worker" — these are the same concepts under earlier names, not different systems. See
[docs/GLOSSARY.md](docs/GLOSSARY.md) for the full current-vs-deprecated mapping before
assuming two terms mean two different things.

## Before you say something is done

- `python -m py_compile $(git ls-files '*.py')`
- `ruff check .`
- `pytest tests/ -q` (full suite, ~5s, 400+ tests, fully offline — no excuse to skip it)
- `bash scripts/check_app_js.sh && node scripts/fe_test.js` if you touched anything
  served to a browser (`passiveworkers/net/dashboard.py`, `passiveworkers/serve.py`'s HTML/JS).
- This project's own culture (see `docs/ROADMAP.md`'s round log) runs an adversarial
  review pass before every commit — green tests are necessary, not sufficient. If
  you're not sure a fix is actually correct, say so rather than reporting it as done.

## Docs honesty rules

- Never claim a capability that isn't wired up yet. If a doc describes a future state,
  label it (`docs/ROADMAP.md`'s pattern, not prose buried in a feature doc).
- Every benchmark number in this repo ships with the script that produced it and its
  limitations stated in the same breath (`docs/TRIAL_RESULTS.md` is the model to
  follow: we publish losses, not just wins). Don't add a number without both.
- If you create a new markdown file, link it from somewhere real (README's doc table,
  llms.txt, or a directly relevant doc) in the same change — an unlinked doc is a dead
  end for both humans and other assistants.
