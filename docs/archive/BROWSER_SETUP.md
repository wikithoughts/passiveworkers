> **Archived 2026-08 — an internal AI-workflow note, not user-facing docs. Kept for history.**

# Browser automation — why it didn't connect, and how it's fixed

When I tried to screenshot the UI earlier, the `claude-in-chrome` tool returned **"Browser extension is
not connected."** Here's the diagnosis and the fix — plus the path I now use that needs no extension.

## What `claude-in-chrome` requires (and the common breakages)
`claude-in-chrome` drives **your** Chrome through the **Claude browser extension**. It only connects when:
1. **The extension is installed** — "Claude in Chrome" from the Chrome Web Store (beta).
2. **Signed into claude.ai with the *same account*** as Claude Code, and the extension has
   **"On all sites"** site access.
3. **Chrome was restarted** after install, and the MCP server shows connected (run `/mcp` in Claude Code
   → `claude-in-chrome` should be green).

The most common reason it silently fails (and the likely one here): **if Claude *Desktop* is also
installed, the extension binds to Desktop's native-messaging host instead of Claude Code's**, so Code
gets "not connected" even though the extension is healthy. Other reports: missing "On all sites"
permission, and a server-side flag that needs a Chrome restart. (Tracked in Anthropic's
`anthropics/claude-code` issues #29528, #24935, #21796, #20887.)

### To enable it (your side, ~2 min)
1. Install **Claude in Chrome** from the Web Store; pin it.
2. Open the extension → sign in with the **same** account as Claude Code; set site access to **On all sites**.
3. If you have **Claude Desktop**, quit it (or it will grab the extension) — or repoint
   `com.anthropic.claude_browser_extension.json` at Claude Code's host.
4. **Fully quit and reopen Chrome.**
5. In Claude Code, run `/mcp` and confirm `claude-in-chrome` is connected.

## What I use instead (no extension needed)
I don't depend on that extension to verify the UI. **Playwright** (its Chromium is already cached on
this machine under `~/Library/Caches/ms-playwright`) drives a headless browser directly:

```bash
source .venv/bin/activate
pip install playwright        # reuses the cached Chromium — no large download
# then a short script launches chromium headless and screenshots each page
```

This is how the screenshots in `docs/preview/` were produced — it works today, offline, with no
sign-in. The `chrome-devtools-mcp` plugin (Chrome DevTools Protocol, also no extension) is an
interactive alternative for live debugging.

**Bottom line:** the extension is a *convenience* for interactive browsing; it is **not required** for
me to render, screenshot, and visually verify the app.
