<!-- fleet-template: v1 | reconciled-against: fleet-kit/templates/AGENT-CONTEXT-TEMPLATE.md @ 08dbc79 2026-09-19 -->
# Passive Workers — instructions for AI coding agents

## What this repo is

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

## Stack & layout

Python 3.10–3.14, pip/`pyproject.toml`, FastAPI/uvicorn, pydantic and pytest/ruff;
inline JavaScript has no separate build toolchain. CLI: `pworkers` (`passiveworkers.cli:main`).
Before changing package layout or imports, read [architecture and terminology](docs/agents/architecture.md).
Optional extras remain lazy-imported: zero-extras `core-install` is a real CI gate.

## Commands

Only commands with a real, grounded source in this repo — nothing invented, none dropped.

**Setup**

```bash
pip install -e .
```
The supported dev install (`requirements.txt`'s own header: `pyproject.toml` is the
real source of truth for deps, this file is a convenience mirror). Add `[dev]`/`[all]`
extras as needed for what you're touching — see `pyproject.toml`'s
`optional-dependencies`.

**Lint**

```bash
ruff check .
```
CI gate is scoped to `select = ["E9", "F"]` (pyflakes + syntax errors only — see
`[tool.ruff.lint]`); style rules aren't enforced yet.

**Typecheck**

```bash
python -m py_compile $(git ls-files '*.py')
```
This is a syntax-compile check, not a real type checker. CI separately runs an
**advisory** `pyright` pass scoped to `passiveworkers/net` only
(`continue-on-error: true`, non-blocking — see `[tool.pyright]` in `pyproject.toml`
and the `types` job in `.github/workflows/ci.yml`); it surfaces signal but doesn't
gate merges yet.

**Test**

```bash
pytest tests/ -q
```
Full suite, fully offline; no excuse to skip it.

**Verify (the lane)**

```bash
ruff check . && pytest tests/ -q --cov=passiveworkers --cov-fail-under=75
```
This is the exact `verify:` string in `.orchestration/lanes.yml` (lint, then the
coverage-gated suite). CI's `test` job enforces the same 75% floor on every Python
version in the matrix, then again scoped to the four thinnest user-facing modules
(`doctor`, `net.agent`, `operator`, `mcp_server`).

**Verify (frontend guard — only if you touched anything served to a browser)**

```bash
bash scripts/check_app_js.sh && node scripts/fe_test.js
```
Needs Node 18+ on `PATH` (CI uses Node 24). Syntax-checks the inline `<script>` blocks
served by `passiveworkers/net/app.py`, `passiveworkers/net/dashboard.py`, and
`passiveworkers/serve.py`. This is a syntax guard, not a real UI check — see
Verification before done for the full check on this VPS.

## Verification before done

- `python -m py_compile $(git ls-files '*.py')`
- `ruff check .`
- `pytest tests/ -q` (full suite, fully offline; add `--cov=passiveworkers
  --cov-fail-under=75` for the CI/lane form above)
- `bash scripts/check_app_js.sh && node scripts/fe_test.js` if you touched anything
  served to a browser (`passiveworkers/net/dashboard.py`, `passiveworkers/serve.py`'s HTML/JS).
- This project's own culture (see `docs/ROADMAP.md`'s round log) runs an adversarial
  review pass before every commit — green tests are necessary, not sufficient. If
  you're not sure a fix is actually correct, say so rather than reporting it as done.

**For anything touching the served dashboard/app/serve HTML or JS**, don't stop at the
syntax guard above: headless Chrome plus Xvfb and the chrome-devtools MCP server are
available on this VPS (wikiclaw-1), so web and admin UI verification — browser-preview,
screenshots, driving `passiveworkers/net/dashboard.py`, `passiveworkers/net/app.py`,
and `passiveworkers/serve.py`'s pages for real — can and should happen from this host.
The only things genuinely unavailable on this VPS are Xcode, iOS/Android simulators,
and native mobile builds; this repo has no use for any of them.

## Guardrails

**Tier: `pr-preferred`** (fleet-wide guarded-repo map,
`~/.claude/hooks/billed_repos.json`) — never push directly to `main`; branch, PR,
squash-merge. See **Git & PR flow** below for the enforcement mechanism.

**`.orchestration/lanes.yml`:** `hot_files: []` and `deploy_on_merge: []` — this repo
currently has neither generated/high-churn hot files nor anything that auto-deploys on
a merge to `main`. (`release.yml` publishes to PyPI, but only on a `v*.*.*` tag push,
never on a merge to `main` — see Git & PR flow.)

**No payments, no Supabase/RLS, no auth backend** to guard here (this repo has no
Supabase project). The real guardrails are architectural/security invariants instead:

- The **give/take rule** and **no proxied traffic** invariants
  (`docs/DECISIONS.md`'s D1/D4), and **D18 (no browser automation / computer-use,
  ever, permanently)** — do not re-propose or silently relitigate any of these three
  without an explicit founder decision. See **Never weaken these...** below for the
  exact files that implement them.
- Every `0600`-mode credential write (signing keys, node/asker/operator secrets) is a
  real vulnerability if weakened, not a style nit — same section below has the file
  list.

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

## Git & PR flow

**Tier: `pr-preferred`.** Never push directly to `main` — branch, PR, squash-merge
(`merge: squash` in `.orchestration/lanes.yml`). Enforced by
the **global** `git-safety-guard.py` hook — this repo's own `.claude/settings.json`
carries only a `SessionStart` fetch hook, no repo-local block-main-commit hook, and
none is needed: the global hook already covers passiveworkers via its `pr-preferred`
entry in `billed_repos.json`.

`force_push: allowed` in `.orchestration/lanes.yml` — unlike some fleet repos,
force-pushing your own branch here is fine. Its `required_checks` is `[ci-ok]`: the
aggregate job in `ci.yml` that needs `changes`, `lint`, `types`, `test` (Python 3.10-3.14),
`frontend` and `core-install`, always runs, and fails if any needed job failed or was
cancelled (skipped is fine; `types` is advisory). GitHub enforces `ci-ok` through a
`required_status_checks` rule on the "fleet: protect default branch" ruleset (alongside
deletion and non-fast-forward). `ci.yml` has no `paths-ignore` on `pull_request`: a
`changes` job skips the heavy jobs for docs-only PRs (every file under `docs/` or `*.md`),
so docs-only PRs still report a passing `ci-ok` and are never blocked. Do not add
`paths-ignore` back to the `pull_request` trigger, and add any new CI job to `ci-ok`'s
`needs` (and gate it on `changes`).

No repo-specific shipper skill exists for passiveworkers — use the fleet-wide `/ship`
skill (branch → commit → PR → squash-merge, guarded-repo aware).

## Host boundaries — VPS vs Mac

Nothing in this repo is Mac-only. There is no `ios/`/`android/` directory, no Xcode
project, and no Expo/EAS native build step — it's a pure Python/FastAPI package plus a
little inline JS served from Python string constants. Every command in Commands above
(lint, the syntax typecheck, the full test suite, the coverage-gated test, the
frontend guard) runs entirely on this VPS.

That includes visual verification of the served UI: headless Chrome plus Xvfb and the
chrome-devtools MCP server are available on this VPS, so browser-preview and
screenshot checks of `passiveworkers/net/dashboard.py`, `passiveworkers/net/app.py`,
and `passiveworkers/serve.py`'s pages can and should happen from this host — don't
stop at the `node --check` syntax guard for a UI-visible change. The only things
genuinely unavailable on this VPS are Xcode, iOS/Android simulators, and native mobile
builds — none of which this repo has any use for.

## Orca conventions

- Update the worktree comment at meaningful checkpoints:
  `orca-ide worktree set --worktree active --comment "<status>" --json`
- Set `--workspace-status in-review` when a PR opens on this repo's work.
- A dispatched worker sends `worker_done` exactly once, with an explicit
  `--outcome`, when finishing supervised orchestration work here — see
  fleet-command's `ORCHESTRATION.md` for the full coordinator recipe.

## Where to find more

- [Docs conventions](docs/agents/docs-conventions.md): vocabulary note and docs honesty rules (read before editing any doc).
No nested `AGENTS.md` files exist in this repo. The only nested context file at all is
the root `CLAUDE.md` stub itself (`@AGENTS.md`, 1 line) — a plain import stub, nothing
to convert.

| Your task touches… | Read first |
|---|---|
| positioning, what this is/isn't | [docs/VISION.md](docs/VISION.md) |
| any architectural decision, especially D1/D4/D18 | [docs/DECISIONS.md](docs/DECISIONS.md) |
| "is X already shipped" | [docs/ROADMAP.md](docs/ROADMAP.md) |
| request handling, credential storage, fetched content | [SECURITY.md](SECURITY.md) |
| old vs current terminology ("Council", "Substrate", "Perspective", "Worker") | [docs/GLOSSARY.md](docs/GLOSSARY.md) |
| benchmark/eval numbers | [docs/TRIAL_RESULTS.md](docs/TRIAL_RESULTS.md), [docs/TRIAL_PROTOCOL.md](docs/TRIAL_PROTOCOL.md) |
| self-hosting a coordinator / the network/commons | [docs/network/](docs/network/), [docs/FEDERATION_V2.md](docs/FEDERATION_V2.md), [docs/CONTRIBUTE_COMPUTE.md](docs/CONTRIBUTE_COMPUTE.md) |
| contributor setup, `[dev]`/`[all]` extras | [CONTRIBUTING.md](CONTRIBUTING.md) |
| machine-readable project index | [llms.txt](llms.txt) |

## Fleet context

This file follows the fleet-wide template
(`fleet-kit/templates/AGENT-CONTEXT-TEMPLATE.md`, stamped above). Config drift between
this file and the template is caught automatically by `fleet-doctor.sh`, which runs as
part of fleet-command's daily sweep — see that repo's `PORTFOLIO.md` and `SWEEP.md` for
what gets reported and what (if anything) gets auto-dispatched.
