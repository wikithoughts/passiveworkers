# Releasing to PyPI

## Current state — **0.5.0 is the latest published release**

`passiveworkers` **0.1.0 through 0.5.0 are live** on https://pypi.org/project/passiveworkers/, each
git-tagged (`v0.1.0`…`v0.5.0`); `0.5.0` was tagged on commit `a507f39` (see `CHANGELOG.md` for what each
release contained — this file does not duplicate it, so it cannot go stale again). Check the live
state before releasing: `curl -s https://pypi.org/pypi/passiveworkers/json | jq -r .info.version`, and
`pyproject.toml`'s `version` for what the next tag must be.

> **Published versions are burned.** A version number on PyPI can never be reused, even after a yank.
> The **next** release MUST bump `version` in `pyproject.toml` (a feature release is a minor bump; a
> pure fix is a patch bump). Note: the PyPI JSON API updates instantly but the pip simple-index lags
> ~30–60s — a fresh `pip install ==<newver>` may 404 right after upload; retry after a minute.

## How to publish — pick ONE path per version, never both

Publishing is **one-shot per version**: the automated path and the manual path below both upload to
the same PyPI project, so running both double-publishes (the second upload is rejected or, with
`skip-existing`, silently skipped). **The automated tag path is the default.**

### Automated (default): push the tag

`.github/workflows/release.yml` runs on any `v*.*.*` tag push: **verify-tag** (the tag commit must be
on `main`, and the tag must equal `v` + the `pyproject.toml` version) → **test** (the `ci-ok` gates:
ruff, pyright, coverage-gated pytest, inline-JS guard) and **core-install** → build + `twine check` →
PyPI publish (Trusted Publishing, OIDC, no stored token) → GitHub Release. One-time prerequisite: a
Trusted Publisher registration on the `passiveworkers` PyPI project (Owner `wikithoughts`, Repo
`passiveworkers`, Workflow `release.yml`, Environment `pypi`).

1. Bump `version` in `pyproject.toml`; add a `CHANGELOG.md` section for it; merge that PR to `main`.
2. **Confirm positioning consistency:** the README hero paragraph, the pyproject.toml
   `description`, and ANNOUNCE.md's opening lines all still match docs/VISION.md's opening
   paragraph (a mismatch is what caused docs/REVIEW_2026-07.md F3/F4) — check it every release.
3. Tag the merged `main` commit and push the tag — **pushing the tag is the publish**:
   ```bash
   git tag -a v<ver> -m "Release <ver> — <one-line summary>"
   git push origin v<ver>
   ```
4. Verify: the Release workflow is green; `curl -s -o /dev/null -w '%{http_code}'
   https://pypi.org/pypi/passiveworkers/<ver>/json` = 200; `pip install passiveworkers==<ver>` in a
   clean venv runs `pworkers version`.

### Manual fallback (only if the workflow is unavailable)

Do **not** push the `v<ver>` tag until the upload is done and verified — the tag push is what triggers
the automated path, so tagging while the workflow is healthy double-publishes. If you publish
manually, push the tag afterwards only as a record and cancel the Release run it triggers.

1. Steps 1–2 above.
2. `rm -rf dist/ build/ *.egg-info && python -m build`; `twine check dist/*` must PASS;
   `tar tzf dist/*.tar.gz` — confirm no secrets bundled; smoke-test the wheel in a throwaway venv.
3. Upload (token never persisted — read from your password manager into the subshell env only):
   ```bash
   TWINE_USERNAME=__token__ \
   TWINE_PASSWORD="$(op item get '<your PyPI token item>' --fields credential --reveal)" \
   twine upload --non-interactive dist/*
   ```
4. Verify as in step 4 above, then tag and push `v<ver>` (cancel the Release run it triggers).

---

### Original prep notes (pre-publish, 2026-06-13)

- `python -m build` → clean wheel + sdist.
- `twine check dist/*` → **PASSED** (both artifacts).
- Name **`passiveworkers` was available** on PyPI and TestPyPI (HTTP 404 = unclaimed). _(Now claimed.)_
- Version **0.1.0**, first public release.

_(Superseded: the original "create an account / .pypirc" steps were removed after 0.1.0 shipped —
the account exists, the token is in 1Password, and we use the env-var upload above, never `.pypirc`.)_
