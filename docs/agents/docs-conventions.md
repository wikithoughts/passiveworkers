# Docs conventions

Moved verbatim from the root `AGENTS.md` (2026-10-05) to keep it under the 12,288-byte context budget.

## Vocabulary note

Before treating old and current terms as different systems, read the
[preserved terminology note](docs/agents/architecture.md#vocabulary-note) and its glossary pointer.

## Docs honesty rules

- Never claim a capability that isn't wired up yet. If a doc describes a future state,
  label it (`docs/ROADMAP.md`'s pattern, not prose buried in a feature doc).
- Every benchmark number in this repo ships with the script that produced it and its
  limitations stated in the same breath (`docs/TRIAL_RESULTS.md` is the model to
  follow: we publish losses, not just wins). Don't add a number without both.
- If you create a new markdown file, link it from somewhere real (README's doc table,
  llms.txt, or a directly relevant doc) in the same change — an unlinked doc is a dead
  end for both humans and other assistants.
