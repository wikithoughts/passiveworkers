#!/usr/bin/env bash
# Guard: syntax-check the inline JavaScript embedded in the served HTML pages, so a
# template-string slip can never ship a page whose <script> fails to parse (which would
# silently kill every event handler). Run this before deploying front-end changes.
set -uo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true
command -v node >/dev/null || { echo "node not found — skipping JS check"; exit 0; }

fail=0
for spec in "council.net.app:APP_HTML" "council.net.dashboard:DASHBOARD_HTML"; do
  mod="${spec%%:*}"; var="${spec##*:}"
  js="$(python3 -c "import re,importlib;m=importlib.import_module('$mod');print(re.findall(r'<script>(.*?)</script>', getattr(m,'$var'), re.DOTALL)[-1])")"
  if printf '%s' "$js" | node --check /dev/stdin; then
    echo "✓ $mod inline JS parses clean"
  else
    echo "✗ $mod inline JS SYNTAX ERROR (see above)"; fail=1
  fi
done
exit $fail
