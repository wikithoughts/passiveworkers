# Releasing to PyPI

## Current state — **0.1.3 (releasing) 2026-06-14**

`passiveworkers` 0.1.0–0.1.2 are live on https://pypi.org/project/passiveworkers/. **0.1.3** ships the
first-run robustness pass + `docs/USE_CASES.md` (R28) plus packaging-metadata hygiene (PEP 639 SPDX
license, expanded classifiers, authors, project URLs) and a `CHANGELOG.md`. Token: 1Password item
**"PyPI API for Claude"** (field `credential`), read into a subshell env at upload time — never written
to `~/.pypirc` or any file. (0.1.0 published 2026-06-13; 0.1.1 + 0.1.2 on 2026-06-14.)

> **Published versions are burned.** A version number on PyPI can never be reused, even after a yank.
> The **next** release MUST bump `version` in `pyproject.toml` (`0.1.4` for fixes, `0.2.0` for features).
> Note: the PyPI JSON API updates instantly but the pip simple-index lags ~30–60s — a fresh
> `pip install ==<newver>` may 404 right after upload; retry after a minute.

## How to publish (repeat for every bumped version)

1. Bump `version` in `pyproject.toml`; add a `CHANGELOG.md` section for the new version; commit.
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
7. **Tag the release** so the published code is checkout-able by tag (one tag per PyPI version):
   ```bash
   git tag -a v<ver> -m "Release <ver> — <one-line summary>"
   git push origin v<ver>
   ```
   (Retroactive tags `v0.1.1` / `v0.1.2` were added in the 0.1.3 round on their release commits.)

---

### Original prep notes (pre-publish, 2026-06-13)

- `python -m build` → clean wheel + sdist.
- `twine check dist/*` → **PASSED** (both artifacts).
- Name **`passiveworkers` was available** on PyPI and TestPyPI (HTTP 404 = unclaimed). _(Now claimed.)_
- Version **0.1.0**, first public release.

_(Superseded: the original "create an account / .pypirc" steps were removed after 0.1.0 shipped —
the account exists, the token is in 1Password, and we use the env-var upload above, never `.pypirc`.)_
