from concurrent.futures import ThreadPoolExecutor

import pytest

from agentsearcher.chunk import Chunk
from agentsearcher.rank import bm25, dedup, rank_chunks, reranker


def test_bm25_prefers_matching_doc_and_handles_inflection():
    docs = ["рецепт борща со свёклой", "столицей Франции является Париж", "погода в Берлине"]
    scores = bm25("столица франции", docs)
    assert scores.index(max(scores)) == 1


def test_dedup_ignores_case_and_punctuation():
    chunks = [Chunk("Hello, World!", "", 0), Chunk("hello world", "", 1), Chunk("other", "", 1)]
    assert [c.source for c in dedup(chunks)] == [0, 1]


STORY = [
    f"The council approved the tram line, stage {i}, after a long and heated public debate."
    for i in range(5)
]


def test_dedup_drops_near_duplicates_and_reprints():
    original = [Chunk("Morning Herald daily news", "", 0)] + [Chunk(t, "", 0) for t in STORY]
    # same story with a different header and a slightly edited sentence
    edited = [STORY[0].replace("heated", "very heated"), *STORY[1:]]
    reprint = [Chunk("Evening Post, subscribe today", "", 1)] + [Chunk(t, "", 1) for t in edited]
    other = [Chunk("Bus routes change next month across the whole northern district.", "", 2)]
    kept = dedup(original + reprint + other)
    assert [c.source for c in kept] == [0] * 6 + [2]


def test_dedup_keeps_source_with_only_shared_boilerplate():
    banner = "We use cookies to improve your experience on this site."
    pages = [
        [Chunk(banner, "", s), Chunk(f"Page {s} explains topic {s} in plenty of detail.", "", s)]
        for s in range(3)
    ]
    kept = dedup([c for page in pages for c in page])
    assert [c.source for c in kept] == [0, 0, 1, 2]


def test_rank_chunks_limits_candidates_per_source():
    chunks = [Chunk(f"python release notes {i}", "", s) for s in range(3) for i in range(5)]
    ranked = rank_chunks("python release", chunks, per_source=2, use_model=False)
    assert len(ranked) == 6
    assert all(0 <= s <= 1 for _, s in ranked)


def test_cross_encoder_multilingual_relevance():
    scores = reranker.score(
        "столица франции",
        ["Париж — столица Франции.", "Рецепт борща со свёклой.", "Paris is the capital of France."],
    )
    assert scores[0] > scores[1]
    assert scores[2] > scores[1]


def test_cross_encoder_concurrent_calls_match_sequential():
    texts = ["Париж — столица Франции.", "Рецепт борща со свёклой."]
    expected = reranker.score("столица франции", texts)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: reranker.score("столица франции", texts), range(8)))
    assert all(r == pytest.approx(expected) for r in results)
