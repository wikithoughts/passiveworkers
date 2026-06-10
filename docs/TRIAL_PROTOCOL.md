# The First Demand Trial — 10 Questions

**Purpose.** Everything is built; the one thing the project has never had is a demand
signal. This trial produces it: you ask 10 real questions in the app, read the council's
answer next to an independent single model's answer, and vote. The win-rate — **by
category** — tells us where the council's value actually is, and that picks the next phase.

This is a measurement, not a demo. A loss in a category is as valuable as a win:
it maps the capability envelope honestly.

## Setup (operator does this once, before you start)

1. VPS hub up (`pw-coordinator` + `pw-worker` systemd units, web research ON).
2. Mac joined: `bash scripts/mac_join.sh` (2 AE perspectives + judge, web research ON).
3. Baseline configured — the "one model" you compare against:
   - Best: `PW_BASELINE_API_KEY` (OpenRouter) in the VPS `.env` → a frontier model,
     your true real-world alternative.
   - Fallback: `PW_BASELINE_LOCAL_MODEL=qwen3:14b` (default; already on the VPS).
   - The compare card shows which one answered (`🌐 model · independent` or
     `🖥 model · independent`). If it says `best council mind`, the independent
     baseline failed — tell the operator before voting.
   - With the local fallback, the baseline is generated AFTER the council finishes
     (the busy CPU host can't run both at once) — the compare card appears one to
     three minutes after the council's answer. Wait for it before voting.
     With an API key it's there immediately.
4. Open the app at `http://127.0.0.1:8791/`.

## The rules (what makes the result honest)

- **Always click "compare" and read the single model's answer in full before voting.**
- Vote **usefulness to you**, not length, not style, not which sounds smarter.
- Don't skip voting on any question — a tie is a valid vote.
- **Finish one question (compare card shown, vote cast) before asking the next.**
  With the local baseline, overlapping questions fight for the same CPU and can
  starve a worker into an empty answer.
- Record the per-question result in the table below as you go (the `/metrics`
  endpoint stores the total, not the category).

## The 10 questions

### A — Geo-sensitive (the moat: in-country web presence) 
1. What do people in Finland actually pay for home electricity right now, and is fixed or spot pricing the better deal this year?
2. What's the current process and real waiting time for a UAE freelance visa, and what does it actually cost all-in?
3. How do everyday people in Finland vs the UAE invest small monthly savings — what do locals actually use?

### B — Research / current events (web research ON)
4. What changed in the EU AI Act's enforcement in the last six months, and who has actually been fined or warned?
5. What are the realistic price and availability trends for used EVs this year — is now a good time to buy?
6. What is the current state of small modular nuclear reactors — who is actually building one, and when does the first one come online?

### C — Reasoning / synthesis (no web edge expected)
7. I can save 1,500/month. Argue for and against: paying down a 4% mortgage faster vs investing in index funds vs keeping cash. What would you do and why?
8. Design a fair way for three co-founders with unequal time commitments to split equity, including what happens if one leaves in year one.

### D — Everyday (the council will probably lose here — that's the point)
9. Give me a simple one-week meal plan for someone who wants high protein, minimal cooking, and a budget.
10. Explain to a 10-year-old why planes can fly.

## Record as you go

| # | Category | Vote (council / single / tie) | One-line reason |
|---|----------|-------------------------------|-----------------|
| 1 | geo | | |
| 2 | geo | | |
| 3 | geo | | |
| 4 | research | | |
| 5 | research | | |
| 6 | research | | |
| 7 | reasoning | | |
| 8 | reasoning | | |
| 9 | everyday | | |
| 10 | everyday | | |

## How to read the result (the decision gate)

- **Council wins geo + research, loses everyday** → expected and good: the moat is real.
  Next: 3rd country node, GeoIP, web research deepened. (Phase G reshaped around geo.)
- **Council wins broadly (≥6/10 decisive)** → demand is wider than the moat.
  Next: Phase F as roadmapped (streaming, installer, Tauri).
- **Council loses or ties nearly everywhere** → the merge/judge layer isn't adding value
  yet. Next: fix merge quality (length target, judge model) BEFORE any scaling or
  recruitment. Cheapest possible time to learn this.

Whatever the outcome: write it into `docs/DECISIONS.md` as **D12** with the table above.
