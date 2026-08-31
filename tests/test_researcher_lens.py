"""R38 (lens-axis) — lens now drives the research-job prompt too, not just chat's
passiveworkers/worker.py::PerspectiveWorker.answer(). Mirrors that file's own
LENSES.get(self.lens, LENSES["neutral"]) fallback pattern in
ResearchWorker._draft(), and locks in backward compatibility for
passiveworkers/local.py, which hardcodes lens="independent analyst" for single-player
`pworkers research` — that string is not a LENSES key, so it must safely fall
through to the neutral instruction rather than raising."""
import passiveworkers.researcher as RW
from passiveworkers.worker import LENSES

EVIDENCE = [{"title": "T", "url": "https://x.test/1", "host": "x.test",
             "snippet": "s", "date_hint": ""}]


def _captured_prompt(monkeypatch, lens):
    captured = {}

    def fake_generate(self, prompt, num_predict):
        captured["prompt"] = prompt
        return "draft [S1].", 5

    monkeypatch.setattr(RW.ResearchWorker, "_generate", fake_generate)
    w = RW.ResearchWorker(worker_id="m", model="m", lens=lens)
    w._draft("a brief about basic chemistry facts", EVIDENCE)
    return captured["prompt"]


def test_draft_prompt_carries_skeptic_lens(monkeypatch):
    prompt = _captured_prompt(monkeypatch, "skeptic")
    assert LENSES["skeptic"] in prompt


def test_draft_prompt_carries_neutral_lens_by_default(monkeypatch):
    # default ResearchWorker.lens == "neutral" — same backward-compat text as worker.py
    prompt = _captured_prompt(monkeypatch, "neutral")
    assert LENSES["neutral"] in prompt


def test_draft_prompt_falls_back_to_neutral_for_local_py_lens_string(monkeypatch):
    # passiveworkers/local.py hardcodes lens="independent analyst" for single-player `pw
    # research` — not a LENSES key. Must not raise, and must fall through to the
    # same neutral instruction LENSES.get(..., LENSES["neutral"]) uses everywhere
    # else. This is the regression test guaranteeing local.py keeps working
    # unmodified (passiveworkers/local.py is intentionally untouched by R38).
    prompt = _captured_prompt(monkeypatch, "independent analyst")
    assert LENSES["neutral"] in prompt
