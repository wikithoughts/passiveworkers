"""M3 refinement (docs/ROADMAP.md, D54): the merge step must TARGET the best single answer's
own length, not just cap output at the longest perspective. Covers: the prompt targets the
best-SCORING answer's length (not merely the longest one), a shorter-than-target merge is passed
through untouched, a longer-but-under-cap merge is passed through untouched, and the hard word
cap still holds — including the citation-marker-safe truncation — when a model ignores the
prompt entirely."""
from passiveworkers.judge import Judge, ScoredCandidate, _enforce_word_cap, _length_band
from passiveworkers.worker import Answer


def _answer(worker_id: str, n_words: int) -> Answer:
    text = " ".join(f"w{i}" for i in range(n_words))
    return Answer(worker_id=worker_id, model="m", lens="x", country="c",
                  text=text, tokens=n_words, elapsed_s=0.1)


# ---------------------------------------------------------------- target = best-scoring, not longest
def test_merge_prompt_targets_best_scoring_length_not_longest(monkeypatch):
    captured = {}
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: captured.update(p=prompt) or "merged")
    j = Judge(model="m")
    # "a" is the LONGEST perspective (250w) but scores worst; "b" is shorter (100w) but scores best.
    answers = [_answer("a", 250), _answer("b", 100)]
    scored = [ScoredCandidate("a", 3.0, "meh"), ScoredCandidate("b", 9.0, "great")]

    j.merge("q", answers, scored=scored)

    lo, hi, hard_cap = _length_band(100)
    assert f"{lo}–{hi} words" in captured["p"]
    assert "≈100 words" in captured["p"]           # targets b's length, not a's 250
    assert f"{hard_cap} words" in captured["p"]


def test_merge_without_scored_falls_back_to_longest(monkeypatch):
    """Backward compatibility: callers (and older tests) that don't pass `scored` keep the prior
    longest-perspective-based target instead of crashing or silently targeting nothing."""
    captured = {}
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: captured.update(p=prompt) or "merged")
    j = Judge(model="m")
    j.merge("q", [_answer("a", 250), _answer("b", 100)])   # no scored=

    lo, hi, hard_cap = _length_band(250)
    assert "≈250 words" in captured["p"]


# ---------------------------------------------------------------- shorter-than-target output
def test_shorter_than_target_output_is_not_padded(monkeypatch):
    # model answers well under the target/tolerance band — merge() must not invent padding.
    short_out = " ".join(f"m{i}" for i in range(30))
    monkeypatch.setattr(Judge, "_generate", lambda self, prompt, num_predict=None: short_out)
    j = Judge(model="m")
    answers = [_answer("a", 250), _answer("b", 100)]
    scored = [ScoredCandidate("a", 3.0, "meh"), ScoredCandidate("b", 9.0, "great")]

    out = j.merge("q", answers, scored=scored)

    assert out == short_out
    assert len(out.split()) == 30


# ---------------------------------------------------------------- longer-than-target, under cap
def test_longer_than_target_but_under_cap_is_left_alone(monkeypatch):
    # target is 100w (lo=85, hi=115, hard_cap=130); 120w is over the soft band but under the cap.
    longish_out = " ".join(f"m{i}" for i in range(120))
    monkeypatch.setattr(Judge, "_generate", lambda self, prompt, num_predict=None: longish_out)
    j = Judge(model="m")
    answers = [_answer("a", 250), _answer("b", 100)]
    scored = [ScoredCandidate("a", 3.0, "meh"), ScoredCandidate("b", 9.0, "great")]

    out = j.merge("q", answers, scored=scored)

    assert out == longish_out
    assert len(out.split()) == 120


# ---------------------------------------------------------------- the hard cap still holds
def test_hard_cap_still_holds_when_model_ignores_the_prompt(monkeypatch):
    # target 100w -> hard_cap 130w; the model returns 500w regardless of the instructions.
    blown_out = " ".join(f"m{i}" for i in range(500))
    monkeypatch.setattr(Judge, "_generate", lambda self, prompt, num_predict=None: blown_out)
    j = Judge(model="m")
    answers = [_answer("a", 250), _answer("b", 100)]
    scored = [ScoredCandidate("a", 3.0, "meh"), ScoredCandidate("b", 9.0, "great")]

    out = j.merge("q", answers, scored=scored)

    _, _, hard_cap = _length_band(100)
    assert len(out.split()) == hard_cap == 130
    assert out.startswith("m0 m1 m2")


def test_hard_cap_holds_even_without_scores(monkeypatch):
    # no scored= given -> target falls back to longest (250w) -> hard_cap = 325w.
    blown_out = " ".join(f"m{i}" for i in range(900))
    monkeypatch.setattr(Judge, "_generate", lambda self, prompt, num_predict=None: blown_out)
    j = Judge(model="m")
    out = j.merge("q", [_answer("a", 250), _answer("b", 100)])

    _, _, hard_cap = _length_band(250)
    assert len(out.split()) == hard_cap


# ---------------------------------------------------------------- _enforce_word_cap unit coverage
def test_enforce_word_cap_is_a_noop_under_the_limit():
    text = "one two three"
    assert _enforce_word_cap(text, 10) == text


def test_enforce_word_cap_truncates_at_word_boundary():
    text = " ".join(str(i) for i in range(20))
    out = _enforce_word_cap(text, 5)
    assert out == "0 1 2 3 4"


def test_enforce_word_cap_drops_a_dangling_citation_opener():
    # cutting at word 4 would land mid-citation ("opens[S1" has '[' with no matching ']' yet);
    # the safety net must drop the dangling opener rather than emit a broken marker.
    text = "alpha beta gamma opens[S1 closes]"
    out = _enforce_word_cap(text, 4)
    assert "[" not in out
    assert out == "alpha beta gamma opens"   # word kept, only the dangling "[S1" marker dropped
