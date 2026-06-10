# Passive Workers — Roadmap

> The living tracker: where we are, what's next, and the bar each step must clear.
> Status legend: ✅ done · ⏭ in progress / next · ◻︎ planned.

## M0 — Phase-0 spikes ✅
De-risk the two pieces most likely to break.
- ✅ **Spike 2** (worker/Ollama wrapper) — hardware profiling, job round-trip, CPU-ceiling pause gate. `spikes/spike2_worker/worker.py`.
- ◻︎ **Spike 1** (verification comparator) — cheat-rejection validated on **one** machine (honest 0.9997 vs cheat 0.4366, threshold 0.82). **Still unverified:** honest *cross-hardware* divergence and *model-downgrade* detection (needs ≥3 heterogeneous machines — now unblocked by M2). `spikes/spike1_fuzzy_verify/verify.py`.

## M1 — Council MVP (local, in-process) ✅
The whole idea at single-machine, multi-model scale.
- ✅ Diverse models → **blind** judge scoring (ideas compete) → diversity-preserving **merge** → non-transferable ledger with **give/take enforcement** and **exact conservation** (rounding bug fixed; 3,000 randomized trials, zero drift).
- ✅ Result: merge beat best-single **2/2** (honest caveat: LLM judge has length bias → see M3). Files: `council/{ledger,worker,judge,coordinator,run_demo}.py`. Run: `python -m council.run_demo`.

## M2 — Networked two-machine Council (Mac ↔ VPS) ✅ (core)
Made the geo-diversity moat real. Coordinator on the Hetzner VPS **wikiclaw-1** (Helsinki, FI),
isolated in `/opt/passiveworkers`, **reusing the host's existing Ollama**, bound to `127.0.0.1`
(zero new public ports); the Mac reaches it over an **SSH tunnel**.
1. ✅ **HTTP coordinator service** — `council/net/coordinator_app.py` + SQLite store (`store.py`); persisted ledger/jobs/nodes/telemetry, config-driven, token-auth. Provider-agnostic (env-only → relocatable).
2. ✅ **Networked worker daemon** — `council/net/agent.py`: register / poll / submit / heartbeat; runs on Mac + VPS.
3. ✅ **Stood up the VPS** — `scripts/deploy_vps.sh` + `scripts/vps_run.sh` (survey-first, isolated, reuse Ollama; Finnish worker on `llama3.2`).
4. ✅ **Cross-country council job** — `scripts/cross_country_demo.sh`: Mac (AE) perspectives + VPS (FI) perspective → judged merge; the FI node genuinely diverged (recommended Western Europe vs the Mac nodes' Southeast Asia). Ledger conserved.
5. ✅ **Telemetry/status** — `GET /status` shows both nodes, country, load, ledger conservation.
6. ✅ **Cross-hardware verification recorded** — `scripts/cross_hardware_verify.py` (Mac/Metal vs VPS/CPU on `gemma3:12b`, vs a `gemma3:4b` downgrade). **Finding: they CONVERGE.** Honest cross-hardware floor **0.8473** vs model-downgrade ceiling **0.8495** → a single fixed fuzzy threshold **cannot** separate honest cross-hardware from a downgrade. (Spike-1's clean 0.82 was an artifact of testing on one machine.) See DECISIONS D10 → verification pivots to **quality/judge-based**, not model-identity.

**Done:** ✅ council runs across both machines, ✅ ledger conserves, ✅ telemetry shows both nodes,
✅ cross-hardware number recorded — with a real finding that reshapes verification (folded into M3).

## M3 — Quality, reputation & the live map ⏭ (mostly done)
- ✅ **Reputation/quality tracking** — each owner accrues a rolling mean judge score; exposed in
  `/status`; **functional**: fleet selection prefers higher-reputation nodes (`store.py`). This is what
  D10's "verify on quality, not model-identity" leans on.
- ✅ **Live operator map (v0)** — `GET /dashboard` (`council/net/dashboard.py`): a Leaflet world map
  placing each node by **country** with live load, model, reputation, last-seen, and recent job flow;
  auto-refreshes from `/status`. (Open it via the tunnel: `http://127.0.0.1:8791/dashboard`.)
- ✅ **Honest merge eval** — `scripts/merge_eval.py`: position-swapped + **length-controlled** pairwise
  comparison. **Finding:** the original merge won 3/3 *raw* but only **1/3 length-controlled** — its edge
  was mostly verbosity. Tightening the merge prompt to synthesize *densely with a length cap* moved the
  honest (length-controlled) win-rate to **2/3**, confirming the diversity dividend is real **per word**.
  The merge now errs *short* (~110w vs ~200w single); next refinement: **target** ≈ best-single length,
  not just cap it.

## Future features (interactivity — founder request)
- **Real IP-based geo on the map.** Today nodes self-report `country`; place them by **GeoIP of the
  connection's source IP** instead (works when nodes connect directly rather than via the SSH tunnel,
  which makes everyone look like `127.0.0.1`). Add a GeoIP lookup at registration.
- **Animate work distribution.** Draw live arcs from the asker → assigned worker nodes → judge as a job
  flows, and pulse nodes while they're computing — show the network "thinking" across the world.
- **Operator leaderboard** on the map (top contributors by reputation / jobs helped) — the BOINC-style
  mission/competition layer that recruits supply.

## M4 — Real per-country web-research ✅
`council/research.py`: a node's own agent researches the live web from its egress (DuckDuckGo
metasearch, `region=wt-wt` so **egress IP drives locale** — the moat), SSRF-guarded, 15-min cached,
Wikipedia fallback, **off by default** (`PW_WEB_BACKEND=ddgs` per node). Returns **owned findings**,
never proxied traffic (D4). Verified live (real 2026 results); wired into the agent.

## M5 — Scale & harden ⏭ (security done)
- ✅ **Security/correctness hardening pass** — 25-agent adversarial review → 18 confirmed findings fixed
  (access control, ledger integrity, robustness); see DECISIONS **D11**. Property-tested.
- ◻︎ **systemd service** for the coordinator + worker (survives reboot; replaces tmux).
- ◻︎ More nodes / a third country; GeoIP + work-flow arcs on the map; then **consider GitHub publish**.

## Phase E — The Living Council Map (end-user experience) ✅
The splendid, map-forward product **and** the first real demand test, served at `GET /`.
- ✅ **The app** (`council/net/app.py`): a self-contained Leaflet world map where nodes glow/arc as
  they deliberate; side panel shows the terse TL;DR, **where minds AGREE vs DIFFER (by country)**, the
  **unique** points, a **vs-single-model** compare, and a **▲/▼ "was the council more useful?"** vote.
- ✅ **Structured judge deliberation** (`judge.deliberate`): one blind call → scores + terse merge +
  `{consensus, disagreements, unique}`.
- ✅ **Real accounts + give/take** (`/users`, `X-User-Secret`): asking debits the user's handle;
  contributing their node credits the same handle. Closes the free-string-asker gap.
- ✅ **THE demand metric**: `POST /jobs/{id}/feedback` + `GET /metrics` → **council-vs-single win-rate**
  (the signal this project never had). Verified live cross-country (Mac AE + Helsinki FI).
- ✅ **Always-on**: systemd units (`scripts/install_systemd.sh`) for coordinator + worker; restart-resilient.
- **Open it:** `bash scripts/mac_join.sh` (or any tunnel) → browse `http://127.0.0.1:8791/`.
- ✅ **Honest compare baseline** (`council/net/baseline.py`): the demand metric's "single model" is now
  an INDEPENDENT answer — a frontier API model (`PW_BASELINE_API_KEY`, OpenRouter) or a strong local
  model (`PW_BASELINE_LOCAL_MODEL`, default qwen3:14b) — generated in parallel with the council and
  stored on the job. The old best-council-answer compare only measured merge-vs-ingredient; it remains
  as a clearly-labelled fallback. Pulled forward from Phase F: a win-rate against a weak baseline would
  have misled the first trial. Web research is ON for trial workers (`PW_WEB_BACKEND=ddgs` in
  `mac_join.sh` + the worker unit). Trial runbook: `docs/TRIAL_PROTOCOL.md`.

## Phase F–H (ahead)
- **F:** ~~compare vs a hosted frontier model~~ (done early — see Phase E); token-streaming; embeddings agreement view; mobile polish.
- **G:** one-command "contribute your PC" installer; GeoIP + deliberation-arc polish; operator leaderboard; 3rd country.
- **H:** publish to GitHub once the demand metric shows signal.

## Later markets (not yet scheduled)
Research-compute commons (batch/science for under-funded labs) · companies running private
multi-node intelligence · broad individual consumer layer.
