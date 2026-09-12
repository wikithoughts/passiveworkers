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


def test_enforce_word_cap_zero_cap_returns_empty_string():
    # cap=0 must not fall through to `matches[cap - 1]` == `matches[-1]` — Python's negative-index
    # wraparound used to silently return the WHOLE text instead of nothing (review PR #22 P2).
    assert _enforce_word_cap("alpha beta gamma", 0) == ""


def test_enforce_word_cap_ignores_earlier_stray_closing_bracket():
    # an earlier stray ']' with no matching '[' must not cancel out a genuine unmatched '[' that
    # the cut itself introduces — a global bracket-COUNT comparison is fooled by this because both
    # totals come out equal (1 '[' vs 1 ']'); an order-aware unmatched-opener scan is not
    # (review PR #22 P2).
    text = "x] alpha beta gamma [S1 tail"
    out = _enforce_word_cap(text, 5)
    assert "[" not in out
    assert out == "x] alpha beta gamma"


# ---------------------------------------------------------------- _length_band literal bounds
def test_length_band_literal_bounds_are_exact():
    # Round-half-up integer-percentage arithmetic (declared rounding rule, judge.py's `_pct_of`)
    # — no floating point, so these exact literals hold rather than being re-derived from the
    # function under test (binary float + int() truncation used to give (85, 114, 130) — one word
    # short of the documented 115% ceiling — review PR #22 P3).
    assert _length_band(100) == (85, 115, 130)
    assert _length_band(200) == (170, 230, 260)


def test_length_band_short_target_clamps_to_the_hard_ceiling_not_the_floor():
    # target=10 -> the true 130% ceiling is 13 words, well under the 60-word MIN_TARGET_WORDS
    # floor meant only to widen the SOFT band. The ceiling must win: the old code returned
    # (60, 60, 60), defeating the documented 130% safety ceiling for short/single answers
    # (review PR #22 P2).
    assert _length_band(10) == (13, 13, 13)


def test_length_band_zero_target_is_fully_zero():
    # explicit zero/empty-input policy: no positive target -> no band at all, rather than the
    # previous self-contradictory "~0 words, 60-60 word band" (review PR #22 P2).
    assert _length_band(0) == (0, 0, 0)


# ---------------------------------------------------------------- explicit zero/empty-input policy
def test_merge_single_empty_answer_targets_zero_and_strips_all_output(monkeypatch):
    # the only candidate is empty -> target 0 -> band (0, 0, 0) -> the word-cap safety net trims
    # any model output to nothing, instead of the old self-contradictory "~0 words, cap 60" prompt.
    monkeypatch.setattr(Judge, "_generate", lambda self, prompt, num_predict=None: "unexpected output")
    j = Judge(model="m")
    out = j.merge("q", [_answer("a", 0)], scored=[ScoredCandidate("a", 9.0, "")])
    assert out == ""


def test_merge_empty_best_scoring_answer_falls_back_to_longest(monkeypatch):
    # an empty highest-scoring answer carries no usable length signal, so the target falls back to
    # the longest candidate rather than collapsing the merge to a 0-word target (documented and
    # tested, not an accidental side effect of Python truthiness — review PR #22
    # "Other findings").
    captured = {}
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: captured.update(p=prompt) or "merged")
    j = Judge(model="m")
    answers = [_answer("a", 0), _answer("b", 250)]
    scored = [ScoredCandidate("a", 9.0, ""), ScoredCandidate("b", 3.0, "")]

    j.merge("q", answers, scored=scored)

    assert "≈250 words" in captured["p"]
    lo, hi, hard_cap = _length_band(250)
    assert f"{lo}–{hi} words" in captured["p"]
    assert f"{hard_cap} words" in captured["p"]


def test_merge_empty_answer_list_defaults_to_200_word_target(monkeypatch):
    # no candidates at all -> the pre-existing `longest(..., default=200)` fallback already
    # applies; made explicit here so the zero/empty policy is documented end-to-end.
    captured = {}
    monkeypatch.setattr(Judge, "_generate",
                        lambda self, prompt, num_predict=None: captured.update(p=prompt) or "merged")
    j = Judge(model="m")

    j.merge("q", [], scored=[])

    assert "≈200 words" in captured["p"]
    lo, hi, hard_cap = _length_band(200)
    assert f"{lo}–{hi} words" in captured["p"]
    assert f"{hard_cap} words" in captured["p"]


# ---------------------------------------------------------------- full-merge citation-cut regression
def test_full_merge_stray_bracket_does_not_mask_a_broken_citation_cut(monkeypatch):
    # Exact reproduction from the PR #22 review: an earlier stray ']' with no matching '[' in the
    # model's own output must not mask a genuine unmatched '[' introduced by the word-cap cut. The
    # source answer carries known [S1]/[S2] markers so they pass `_drop_invented_markers`; the
    # model then emits a stray "x]" before 128 filler words, then the real "[S1, S2]" marker,
    # which the 130-word cap slices through.
    source = _answer("a", 98)
    source.text = source.text + " [S1] [S2]"
    model_output = "x] " + " ".join(["word"] * 128) + " [S1, S2] tail"
    monkeypatch.setattr(Judge, "_generate", lambda self, prompt, num_predict=None: model_output)
    j = Judge(model="m")

    out = j.merge("q", [source], scored=[ScoredCandidate("a", 9.0, "")])

    # the leading "x]" is ordinary text, not a citation — it must survive untouched. Only the
    # genuinely dangling "[S1," marker the cut introduced gets dropped.
    assert not out.rstrip().endswith("[S1,")
    assert "[" not in out
    assert out == "x] " + " ".join(["word"] * 128)
