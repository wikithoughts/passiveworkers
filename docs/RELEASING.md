# Releasing to PyPI

The package is **publish-ready and verified** — the only missing piece is a PyPI API token, which is
tied to your PyPI account (login + 2FA) and can't be created for you.

## Verified state (as of 2026-06-13)

- `python -m build` → clean wheel + sdist.
- `twine check dist/*` → **PASSED** (both artifacts).
- Name **`passiveworkers` is available** on PyPI and TestPyPI (HTTP 404 = unclaimed).
- Version **0.1.0**, never published. (First public release — 0.1.0 is correct.)

## Publish (one-time setup + upload)

1. **Create a PyPI account + API token:** pypi.org → Account settings → API tokens → *Add API
   token* (scope: "Entire account" for the first upload, then narrow to the project). Copy the
   `pypi-…` token.

2. **Give twine the token** (either way):

   ```bash
   # option A: environment (per-shell)
   export TWINE_USERNAME=__token__
   export TWINE_PASSWORD=pypi-AgEI...your-token...

   # option B: ~/.pypirc (persistent)
   cat > ~/.pypirc <<'EOF'
   [pypi]
     username = __token__
     password = pypi-AgEI...your-token...
   EOF
   chmod 600 ~/.pypirc
   ```

3. **(Recommended) dry run on TestPyPI first:**

   ```bash
   python -m build
   twine upload --repository testpypi dist/*
   pip install -i https://test.pypi.org/simple/ passiveworkers   # smoke test in a fresh venv
   ```

4. **Publish to real PyPI:**

   ```bash
   python -m build              # rebuild so the artifacts match the committed code
   twine check dist/*
   twine upload dist/*
   ```

   Then `pip install passiveworkers` works for everyone.

## After publishing

- A version number on PyPI **cannot be reused** — even if you delete a release, `0.1.0` is burned.
  So only upload when the artifacts are the ones you want public. Bump to `0.1.1` (or `0.2.0`) in
  `pyproject.toml` for the next release.
- The README's install line already says `pip install passiveworkers`; nothing else to change.
