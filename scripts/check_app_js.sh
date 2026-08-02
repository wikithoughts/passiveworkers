#!/usr/bin/env bash
# Guard: syntax-check the inline JavaScript embedded in the served HTML pages, so a
# template-string slip can never ship a page whose <script> fails to parse (which would
# silently kill every event handler). Run this before deploying front-end changes.
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true
command -v node >/dev/null || {
  echo "✗ node not found — this gate needs Node 18+ on PATH (brew install node / apt install nodejs)." >&2
  echo "  Refusing to report a false green; install Node and re-run." >&2
  exit 1
}

fail=0
tmpdir="$(mktemp -d)"
tmp="$tmpdir/check.js"   # must end in .js so node treats it as a script
trap 'rm -rf "$tmpdir"' EXIT
for spec in "council.net.app:APP_HTML" "council.net.dashboard:DASHBOARD_HTML" "council.serve:SERVE_HTML"; do
  mod="${spec%%:*}"; var="${spec##*:}"
  # write to a real temp file — `node --check /dev/stdin` can't read a pipe on Linux CI
  python3 -c "import re,importlib;m=importlib.import_module('$mod');open('$tmp','w').write(re.findall(r'<script>(.*?)</script>', getattr(m,'$var'), re.DOTALL)[-1])"
  if node --check "$tmp"; then
    echo "✓ $mod inline JS parses clean"
  else
    echo "✗ $mod inline JS SYNTAX ERROR (see above)"; fail=1
  fi
done
exit $fail
