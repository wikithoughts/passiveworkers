# Releasing to PyPI

## Current state — **0.4.0 PUBLISHED (2026-07-10)**
`passiveworkers` **0.1.0 through 0.4.0 are live** on https://pypi.org/project/passiveworkers/, each
git-tagged (`v0.1.0`…`v0.4.0`). **0.4.0** (R36/D52) is a broad round: web-evidence **rerank** +
**adaptive/recursive** research depth, plus a hardening sweep (the escrow-refund atomicity fix, one shared
UI theme in `passiveworkers/net/ui_common.py`, scoped advisory `pyright`, and tests for the uncovered web-UI /
legacy-`Council` surfaces). Published from commit `555b27c`, verified (PyPI JSON API 200 + a clean-venv
`pip install passiveworkers==0.4.0` → `pworkers version` 0.4.0), tagged `v0.4.0`, and adversarially reviewed
before commit (2 real issues caught + fixed). **The next release MUST bump the version** — `0.4.0` is now
burned on PyPI.

_Prior: **0.3.0** (2026-07-10, commit `c2921f2`, R35/D51) — inference-time citation-fidelity self-repair
in `pworkers research` + a free local currency baseline (`--baseline local`) with a re-verified/expanded bank._

_Prior: **0.2.0** (2026-07-07, commit `4963906`) bundled R32/D48 (security/privacy hardening, engine
reliability, `pworkers status`/`--json`/`--html`, marketplace account recovery, CI matrix), R33/D49
(`pworkers config` + keyed search backends), and R34/D50 (pricing dedup, shared UI module, operator/asker CLI
test coverage, the `pworkers rate` fix)._

Token: 1Password item
**"PyPI API for Claude"** (field `credential`), read into a subshell env at upload time — never written
to `~/.pypirc` or any file.

> **Published versions are burned.** A version number on PyPI can never be reused, even after a yank.
> The **next** release MUST bump `version` in `pyproject.toml` (`0.5.0` for the coming feature release;
> a `0.4.x` bump only for a pure fix). Note: the PyPI JSON API updates instantly but the pip
> simple-index lags ~30–60s — a fresh `pip install ==<newver>` may 404 right after upload; retry after a minute.

## How to publish (repeat for every bumped version)

1. Bump `version` in `pyproject.toml`; add a `CHANGELOG.md` section for the new version; commit.
2. **Confirm positioning consistency:** the README hero paragraph, the pyproject.toml
   `description`, and ANNOUNCE.md's opening lines all still match docs/VISION.md's opening
   paragraph. A mismatch here is exactly what caused docs/REVIEW_2026-07.md F3/F4 (the repo
   disagreeing with itself about whether single-player research or the network is "the
   product") — check it every release, not just when VISION.md changes.
3. `rm -rf dist/ build/ *.egg-info && python -m build` (rebuild so artifacts match committed code).
4. `twine check dist/*` → must PASS; `tar tzf dist/*.tar.gz` → confirm no secrets bundled.
5. Smoke-test the wheel in a throwaway venv: `pip install dist/*.whl` → `pworkers --help` works.
6. Upload (token never persisted):
   ```bash
   TWINE_USERNAME=__token__ \
   TWINE_PASSWORD="$(op item get 'PyPI API for Claude' --fields credential --reveal)" \
   twine upload --non-interactive dist/*
   ```
7. Verify: `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/passiveworkers/<ver>/json`
   = 200, and `pip install passiveworkers==<ver>` in a clean venv runs `pworkers`.
8. **Tag the release** so the published code is checkout-able by tag (one tag per PyPI version):
   ```bash
   git tag -a v<ver> -m "Release <ver> — <one-line summary>"
   git push origin v<ver>
   ```
   (Retroactive tags `v0.1.1` / `v0.1.2` were added in the 0.1.3 round on their release commits.)

**Automated alternative (R37):** `.github/workflows/release.yml` runs the test → build →
`twine check` → PyPI-publish (Trusted Publishing, OIDC, no stored token) → GitHub Release
sequence automatically on any `git push origin v<ver>` tag. It still needs step 1-2 done and
committed first, and a one-time Trusted Publisher registration on the `passiveworkers` PyPI
project (Owner `wikithoughts`, Repo `passiveworkers`, Workflow `release.yml`, Environment
`pypi`) before its first use. Steps 3-7 above become optional manual verification once that's
set up — pushing the tag *is* the publish.

---

### Original prep notes (pre-publish, 2026-06-13)

- `python -m build` → clean wheel + sdist.
- `twine check dist/*` → **PASSED** (both artifacts).
- Name **`passiveworkers` was available** on PyPI and TestPyPI (HTTP 404 = unclaimed). _(Now claimed.)_
- Version **0.1.0**, first public release.

_(Superseded: the original "create an account / .pypirc" steps were removed after 0.1.0 shipped —
the account exists, the token is in 1Password, and we use the env-var upload above, never `.pypirc`.)_
