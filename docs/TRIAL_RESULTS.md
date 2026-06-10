# First Demand Trial — Results (2026-06-10)

**Headline: the council won 0/10 against a frontier single model (gpt-5-chat). 7 losses, 3 ties.**
`GET /metrics`: `{"council": 0, "single": 7, "tie": 3, "total": 10, "council_win_rate": 0.0}`

This is the demand signal the project never had. It is negative for the current quality bar —
and it is exactly the measurement we built everything to get.

## How it was run (docs/TRIAL_PROTOCOL.md)

- Live cross-country council: Mac/AE (gemma3:4b + gemma2:9b) + VPS/FI (llama3.2-3B, web research ON),
  judge qwen2.5:14b on the Mac. 10 protocol questions, one at a time (`scripts/run_trial.py`).
- Baseline: **openai/gpt-5-chat** via OpenRouter — the asker's real-world alternative — generated
  independently per question.
- Verdicts (`scripts/judge_trial.py`): two judges, both blind, council/baseline randomly assigned A/B
  per question. Judge A = local qwen2.5:14b, each pair judged twice with positions swapped (consistent
  winner or tie). Judge B = Claude (Fable 5), verdicts committed before opening the mapping.
  Judges disagree → tie. Votes cast through the real `/jobs/{id}/feedback` endpoint.

## Per-question results

| # | Category | Judge A (qwen) | Judge B (Claude) | Final |
|---|----------|----------------|------------------|-------|
| 1 | geo — FI electricity prices | single | single | **single** |
| 2 | geo — UAE freelance visa | single | single | **single** |
| 3 | geo — FI vs UAE small savings | single | single | **single** |
| 4 | research — EU AI Act enforcement | single | **council** | tie |
| 5 | research — used EV market | single | **council** | tie |
| 6 | research — SMR reactors | single | single | **single** |
| 7 | reasoning — 1,500/mo allocation | single | single | **single** |
| 8 | reasoning — co-founder equity | single | single | **single** |
| 9 | everyday — meal plan | single | single | **single** |
| 10 | everyday — planes for a 10-year-old | tie | single | tie |

Judge A was position-consistent on 9/10 pairs. The judges agreed on 7/10.

## What the answers actually showed

1. **The frontier baseline simply out-answered the council on substance.** gpt-5-chat gave the UAE
   visa process with itemized AED costs and timelines; the council's merge said "costs vary widely,
   weeks to months." On SMRs the council's merge contained real factual errors (called the AP1000 an
   SMR, garbled NuScale's status); the baseline was accurate end-to-end.
2. **The council's ONLY edge was currency — the moat working.** On the two questions where being
   up-to-date in 2026 mattered (EU AI Act enforcement, used-EV market), the council's web-researched
   answers were current and cited sources, while gpt-5-chat confidently answered from its training
   data ("As of early 2024…"). The blind frontier-class judge (B) picked the council both times; the
   local judge (A), whose own knowledge is frozen, could not see the staleness and picked the
   fluent stale answer. Currency is invisible to most judges — but not to a real asker in 2026.
3. **A merge bug surfaced (now fixed):** question 9's merged answer leaked internal deliberation
   ("Answer 1 and 3 focus on… Answer 2 cautions…") instead of producing a deliverable. The judge
   merge prompt now forbids referring to the existence of multiple answers.
4. **3-to-4B-class workers cannot out-substance a frontier model, merged or not.** This was the
   known honest crux ("never pitch cheaper/better intelligence"); the trial quantifies it: 0/10.

## Limitations

- n=10, two LLM judges, no human votes yet — the founder should repeat the protocol in the app
  (`http://127.0.0.1:8791/` after `scripts/mac_join.sh`) for the human signal.
- Both judges have their own biases; the tie-on-disagreement rule is conservative.
- The trial ran the merge-length fix but pre-dated the merge-leak fix (Q9).

## What this steers (recorded as D12 in DECISIONS.md)

Per the decision gate: "council loses or ties nearly everywhere → fix the value layer before any
scaling or recruitment." Specifically:

1. **Stop competing on general answer quality vs frontier models** — structurally unwinnable with
   3–9B local workers, and the constraint D3 predicted this. The compare stays in the app (honesty),
   but the pitch and the product must lead with what the frontier cannot do.
2. **Double down on the one observed edge: live, geo-localized, cited web research.** Deepen it
   (more sources per country, citations surfaced in the UI, freshness shown). The council should WIN
   "what is true right now, here" questions every time — that is the moat.
3. **Raise the council's substance floor**: add at least one strong local mind (qwen3:14b or
   mistral-small:22b on capable machines) so merges have a quality anchor.
4. **Fix observed merge defects** (leak fixed; factual-error rate needs the stronger anchor mind).
5. Privacy/commons/sovereignty remain real differentiators the trial did not measure.
