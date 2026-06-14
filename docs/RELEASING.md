# Releasing to PyPI

## Current state — **0.1.1 PUBLISHED 2026-06-14** ✅

`passiveworkers` **0.1.1 is live**: https://pypi.org/project/passiveworkers/0.1.1/ — `pip install
passiveworkers` gets the full distributed-network track (R20–R25: scheduler/failover/progress,
download-extract + code-generation task types, fetch/upload hardening, stage chaining, rate limiting,
per-operator enrollment tokens) on top of the 0.1.0 single-player research engine. Verified end-to-end
(twine check PASSED, sdist secret-scanned, clean-venv install from PyPI exposes a working `pw`). Token:
1Password item **"PyPI API for Claude"** (field `credential`), read into a subshell env at upload time
— never written to `~/.pypirc` or any file. (0.1.0 published 2026-06-13.)

> **Published versions are burned.** A version number on PyPI can never be reused, even after a yank.
> The **next** release MUST bump `version` in `pyproject.toml` (`0.1.2` for fixes, `0.2.0` for features).
>
> **Next-bump cleanup (cosmetic):** switch `pyproject.toml` to the SPDX form `license = "MIT"` (drop
> `license = { file = "LICENSE" }`) so PyPI shows a clean `license_expression` instead of embedding
> the full license text in the metadata field.

## How it was published (repeat for the next bumped version)

1. Bump `version` in `pyproject.toml`, commit.
2. `rm -rf dist/ build/ *.egg-info && python -m build` (rebuild so artifacts match committed code).
3. `twine check dist/*` → must PASS; `tar tzf dist/*.tar.gz` → confirm no secrets bundled.
4. Smoke-test the wheel in a throwaway venv: `pip install dist/*.whl` → `pw --help` works.
5. Upload (token never persisted):
   ```bash
   TWINE_USERNAME=__token__ \
   TWINE_PASSWORD="$(op item get 'PyPI API for Claude' --fields credential --reveal)" \
   twine upload --non-interactive dist/*
   ```
6. Verify: `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/passiveworkers/<ver>/json`
   = 200, and `pip install passiveworkers==<ver>` in a clean venv runs `pw`.

---

### Original prep notes (pre-publish, 2026-06-13)

- `python -m build` → clean wheel + sdist.
- `twine check dist/*` → **PASSED** (both artifacts).
- Name **`passiveworkers` was available** on PyPI and TestPyPI (HTTP 404 = unclaimed). _(Now claimed.)_
- Version **0.1.0**, first public release.

_(Superseded: the original "create an account / .pypirc" steps were removed after 0.1.0 shipped —
the account exists, the token is in 1Password, and we use the env-var upload above, never `.pypirc`.)_
