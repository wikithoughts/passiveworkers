"""Retrieval primitives: BM25 lexical scoring + reciprocal rank fusion (R8)."""
from council.retrieval import BM25Okapi, tokenize, reciprocal_rank_fusion

CORPUS = [
    "project polaris ships september budget aed",
    "the cat sat on the mat in fine weather",
    "polaris gpu supplier risk mitigation dual source",
    "quarterly revenue grew across all regions",
]


def test_bm25_ranks_relevant_first():
    bm = BM25Okapi([tokenize(c) for c in CORPUS])
    top = bm.top("polaris budget", k=2)
    assert 0 in top and 1 not in top   # budget doc in, cat doc out


def test_bm25_exact_rare_term():
    # dense embeddings blur rare tokens; BM25 nails them
    bm = BM25Okapi([tokenize(c) for c in ["the AED4200000 ceiling", "general budget talk"]])
    assert bm.top("AED4200000", k=1)[0] == 0


def test_bm25_empty_corpus():
    bm = BM25Okapi([])
    assert bm.scores("anything") == []
    assert bm.top("x", k=3) == []


def test_rrf_fuses_rankings():
    fused = reciprocal_rank_fusion([[3, 0, 2, 1], [0, 2, 3, 1]], top_k=3)
    assert fused[0] in (0, 3) and 1 not in fused   # worst-in-both excluded from top3


def test_rrf_rewards_agreement():
    # an item ranked high in BOTH lists should win over one high in only one
    fused = reciprocal_rank_fusion([[5, 9], [5, 8]])
    assert fused[0] == 5
