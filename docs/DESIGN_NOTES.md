# Passive Workers — Design Notes (proposed, decided *with data*)

> Ideas from the founder's product feedback, captured with honest recommendations + open
> questions, to prioritize **after the first real trial** (the council-vs-single win-rate).
> Nothing here is built yet. Order will be set by what the trial shows.

## 1. Judge placement
**Proposal:** make merging optional and explicit, not automatic.
- Default for a *weak* asker: **raw / no-merge** — just return the N individual answers (we now show
  these). A good *structured* judge needs a ~14B-class model; requiring the consumer to run it
  contradicts "works on any machine," so don't force it.
- When the asker's machine *is* capable: **judge on the asker's own computer** (private, no extra cost,
  full control). Otherwise a cheap judge node.
- Always show **who judged** (machine + country) — already surfaced as `judge_machine_key`/`judge_country`.
**Open:** is a fixed-threshold "is this a good merge?" check worth it, or just let the user pick/merge?

## 2. Responder dial (how many / which minds)
**Proposal:** the asker chooses **how many minds** (1 / N / pick-on-map) and the **mode**:
- `best` = fastest + closest + highest-reputation; `random`; or **pick specific computers** on the map.
- Cost scales with count — this *is* the honest cost↔quality dial (asking 10 minds costs ~10×; you
  choose because the 10-mind answer is worth more).
- See each answer individually (done), then **keep / drop / merge** parts yourself.
**Build notes:** `create_job` takes `n_responders` + a `selector` (countries / machine_keys / mode);
the scheduler already does top-N selection — extend it.

## 3. Economics — the asymmetry (the hardest, NOT solved)
**The concern (valid):** with compute-time parity, asking K minds costs ~K× what you earn per unit of
your own contribution. **Leading proposal:** a compute-cost **floor** for contributors (transparent —
"your PC gave X tokens in Y s, earned Z"), with a **value-based price** at the edge for high-value/B2B
asks.
**Two open questions that must be answered before building it:**
1. **Conservation.** A closed, non-tradeable credit system conserves credits (earned == spent), so the
   council genuinely *costs* K× the compute no matter who pays. Value-pricing redistributes *who* pays;
   it doesn't erase the cost. → The multi-mind model only makes sense if the council is **genuinely
   better** — which the trial tests. If it isn't, default to fewer minds / raw mode.
2. **Where the premium goes.** The (value-price − compute-floor) spread must go to the **platform as a
   fee**. If it went to contributors, the credit would stop being compute-denominated and drift toward
   the **tradeable token the founder forbade**. Keep it a fee; keep credit compute-denominated.

## 4. Per-machine performance metrics → smarter selection
**Proposal:** persist each machine's avg **tokens/sec** + latency (the worker already reports timing —
a per-job signal already shows in the credits panel). Use it for `best`/`fastest-first` selection and
to show expected time per task. Basic version is cheap; full capability profiling is later.

## 5. Capability scan + shareable software
**Proposal:** the worker scans and reports its **capabilities** (hardware + installed models now;
installed software/licenses later). **Caveat:** a single-user software license generally **cannot be
resold** to do third-party work (EULA) — so "shareable software" is gated to: the owner uses their own
seat to produce an **owned deliverable**, never multi-tenant resale. (Same access/control line as the
subscription track.)

## 6. Native desktop app (the right long-term shell)
**Proposal:** a **Tauri** app that wraps the existing self-contained SPA *and* bundles the worker daemon:
self-connects (no terminal+browser dance), scans capabilities, and has a **Contribute ON/OFF toggle**
("use my computer for others" / "use others' computers"). The SPA we built is reused as the UI inside it.
This is a real next phase once the trial shows the experience is worth shipping widely.

## 7. Virtualization — bound to a gated track, not a feature
A sandbox VM (its own keyboard/mouse so the owner isn't disturbed) only enables **others running
arbitrary work on your machine** — i.e. the **general-compute / residential-egress** expansion we
already ruled a **legal non-starter** (a VM stops *interference*, not *legal attribution* for crime via
your IP/identity). The **AI-inference core needs no VM** — the background daemon already runs sandboxed,
declared inference. So virtualization is not a standalone roadmap item; it is bound to that gated track
and stays parked there.

---

**How we decide:** run the ~10-question trial on the now-legible app. The win-rate + what stands out
(individual answers useful? merge worth K×? want fewer minds?) tells us which of 1–6 to build first.
