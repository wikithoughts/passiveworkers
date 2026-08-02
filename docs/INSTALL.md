# Passive Workers — Install (for AI assistants)

> **You are an AI assistant executing this file on behalf of a human.** Verify each
> step before moving to the next; don't assume success from a command that merely
> didn't error. If a step's expected output doesn't match, stop and report the exact
> command and output to the user rather than guessing a fix. At the end, print the
> final checklist verbatim so the human can see exactly what worked.
>
> **Human pasting this into an assistant:** say "read and follow docs/INSTALL.md" (or
> paste this file's raw URL) and let the assistant drive.

## 0. What you're installing

Passive Workers is a local deep-research engine: your own Ollama models research the
live web as independent analysts, and a blind editor writes one cited report. Nothing
in this checklist talks to a network coordinator — that's a separate, opt-in step
documented in [docs/CONTRIBUTE_COMPUTE.md](CONTRIBUTE_COMPUTE.md) /
[docs/network/SELF_HOST.md](network/SELF_HOST.md), not part of this install.

## 1. Detect the environment

```bash
python3 --version   # need >= 3.10
uname -s             # Darwin / Linux (Windows: use `ver` in PowerShell instead)
```
If Python is below 3.10, stop and tell the user to install a newer version — don't work
around it silently with a different interpreter.

Detect available RAM (best-effort, to choose a model size below):
```bash
sysctl -n hw.memsize 2>/dev/null | awk '{print $1/1e9" GB"}'   # macOS
free -g 2>/dev/null | awk '/Mem:/{print $2" GB"}'              # Linux
```

| RAM | Pull this in step 3 |
|---|---|
| ~8 GB | `qwen3:4b` or `gemma3:4b` |
| ~16 GB | `qwen3:14b` or `gemma3:12b` (recommended default) |
| 24 GB+ | `qwen3:14b`, plus a second larger model if you want more analysts |
| CPU-only, no GPU | any of the above still works, just slower (~3–6 tok/s) — set `PW_MODEL_CAP_GB=3` later if it's too slow |

## 2. Install and start Ollama

```bash
curl -sf http://127.0.0.1:11434/api/tags >/dev/null && echo "Ollama already running" || echo "need to install/start Ollama"
```
If not running: install from https://ollama.com/download (macOS/Windows: the
installer; Linux: `curl -fsSL https://ollama.com/install.sh | sh`), then:
```bash
ollama serve &
curl -sf http://127.0.0.1:11434/api/tags && echo OK
```
Expected: `OK` after a JSON body (`{"models":[]}` is fine — nothing pulled yet).

## 3. Pull a model

```bash
ollama pull qwen3:14b   # or the size from the RAM table above
```
Expected: ends with `success`. If it errors on disk space, pick a smaller model and
retry — don't silently substitute a larger one instead. For private-document research
(step 7 in USE_CASES) also pull the embedder: `ollama pull nomic-embed-text`.

## 4. Install Passive Workers

```bash
pip install 'passiveworkers[all]'
```
`[all]` is intentional — it includes clean page extraction, private-document RAG, and
the MCP server extra. A bare install works but degrades extraction quality; don't
recommend it unless the user explicitly wants a minimal footprint.

Expected: `pw --help` prints the command list. If `pw` isn't on PATH afterward, check
pip's output for a PATH warning (common with some venv/pipx setups) and fix PATH rather
than reinstalling.

## 5. Run `pw status` and remediate anything red

```bash
pw status
```
Expected shape (model names/counts will differ):
```
Passive Workers v0.4.0 — status

  ✓ Ollama reachable at http://localhost:11434 — 1 model(s)
      analysts (up to 3): qwen3:14b
      editor / judge:     qwen3:14b
  ✓ Web search: ddgs
  · Library: empty — index your own files with `pw library add <path>`
  · Embedder: nomic-embed-text not pulled — `ollama pull nomic-embed-text` (needed for `pw library add` / `--local` research)
  · Not joined — `pw join <url> <token>` to contribute this machine
  · Reports: 0 in /Users/you/.passiveworkers/reports

  Ready to research.
```
Do not proceed to step 6 until the top line (Ollama) shows `✓`. Remediation by symptom:

| Symptom | Fix |
|---|---|
| `✗ Ollama: Can't reach Ollama at …` | Return to step 2 — Ollama isn't running |
| `✗ Ollama: No usable Ollama models found` | Return to step 3 — nothing pulled yet |
| `⚠ Web search: … selected but … not set` | Ignore it (DuckDuckGo is the free fallback), or set the named key: `pw config set PW_<BACKEND>_KEY <value>` |
| `· Embedder: not pulled` | Only matters for `pw library`/`--local` research — `ollama pull nomic-embed-text` |
| `· Not joined` | Expected and fine — this is the single-player install; joining the network is a separate, opt-in doc |

## 6. Smoke test

```bash
pw research "What is retrieval-augmented generation?" --quick --analysts 1
```
Expected: a progress line per analyst, then `📄 Report ready in … → reports/....md`.
Verify at least one citation:
```bash
ls reports/*.md | tail -1 | xargs grep -c '\[S[0-9]'
```
Expected: a number ≥ 1. A `0` means the run had zero web sources (most likely a
rate-limited search backend) — the run itself now refuses to compile a report when
this happens (it exits non-zero with a fix hint), so a completed run here already
guarantees at least one real citation. Report a non-zero exit to the user; don't
silently retry more than once.

## 7. Optional: register as an MCP tool

If you (the assistant) are an MCP host, or the user's MCP-capable client should call
this engine directly:
```bash
pw mcp --help >/dev/null 2>&1 || echo "install the mcp extra: pip install 'passiveworkers[mcp]'"
```
Add to the host's config (e.g. Claude Desktop's `claude_desktop_config.json`):
```json
{ "mcpServers": { "passive-workers": { "command": "pw", "args": ["mcp"] } } }
```
Restart the client and confirm three tools appear: `research`, `library_search`,
`library_add`. The `research` tool returns a structured result
(`{report, sources_web, sources_local, n_sources, analysts_used, depth_requested,
depth_achieved, error}`) — always check `error` is `null` before trusting `report`.

## 8. Final checklist (echo back to the user, filled in)

```
[ ] Python >= 3.10 confirmed:        <version>
[ ] Ollama running:                  yes/no
[ ] Model pulled:                    <model name>
[ ] pip install 'passiveworkers[all]' succeeded
[ ] pw status reports "Ready to research."
[ ] Smoke-test report generated with >=1 citation
[ ] (optional) MCP registered:       yes/no/not attempted
```
If anything is "no" or unresolved, say so explicitly — never report the install as
complete with unresolved items.
