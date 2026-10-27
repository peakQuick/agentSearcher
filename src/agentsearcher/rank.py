import logging
import math
import re
import threading
import time
from collections import Counter

import numpy as np

from agentsearcher import config
from agentsearcher.chunk import Chunk

log = logging.getLogger(__name__)

TOKEN = re.compile(r"\w+")
STEM = 6
BATCH = 16
MAX_TOKENS = 192
MAX_QUERY_TOKENS = 48
RERANK_REPO = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANK_FILE = "onnx/model_quint8_avx2.onnx"
BOS_ID, PAD_ID, EOS_ID, UNK_ID = 0, 1, 2, 3
SHINGLE = 5
NEAR_DUP = 0.8
REPRINT = 0.8


def tokenize(text: str) -> list[str]:
    # crude prefix stemming: good enough for inflected languages like Russian
    return [t[:STEM] for t in TOKEN.findall(text.lower()) if len(t) > 1]


def bm25(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    terms = set(tokenize(query))
    tokenized = [tokenize(d) for d in docs]
    if not terms or not docs:
        return [0.0] * len(docs)
    avg_len = sum(map(len, tokenized)) / len(docs) or 1.0
    df = Counter(t for doc in tokenized for t in set(doc) if t in terms)
    scores = []
    for doc in tokenized:
        tf = Counter(doc)
        score = 0.0
        for t in terms:
            if tf[t]:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(doc) / avg_len))
        scores.append(score)
    return scores


class CrossEncoder:
    """XLM-R based ONNX cross-encoder, loaded on first use and dropped after IDLE_TTL idle.

    Tokenizes with sentencepiece instead of HF `tokenizers`: same ids for XLM-R
    (fairseq offset +1), but ~60 MB of RAM instead of ~280 MB for the 250k vocab.
    """

    def __init__(self):
        self._session = None
        self._spm = None
        self._lock = threading.Lock()
        self._last_used = 0.0

    def _load(self):
        import onnxruntime as ort
        import sentencepiece
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        def download(filename: str) -> str:
            try:
                return hf_hub_download(RERANK_REPO, filename, local_files_only=True)
            except LocalEntryNotFoundError:
                return hf_hub_download(RERANK_REPO, filename)

        log.info("loading reranker %s", RERANK_REPO)
        options = ort.SessionOptions()
        options.intra_op_num_threads = config.RERANK_THREADS
        options.enable_cpu_mem_arena = False
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        self._session = ort.InferenceSession(
            download(RERANK_FILE), options, providers=["CPUExecutionProvider"]
        )
        self._spm = sentencepiece.SentencePieceProcessor(
            model_file=download("sentencepiece.bpe.model")
        )

    @staticmethod
    def _ids(spm, text: str) -> list[int]:
        return [i + 1 if i else UNK_ID for i in spm.encode(text)]

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Concurrent calls run in parallel: the lock only guards loading and unloading,
        and a call keeps its own references, so an unload mid-call can't break it."""
        with self._lock:
            if self._session is None:
                self._load()
            session, spm = self._session, self._spm
            self._last_used = time.monotonic()
        query_ids = self._ids(spm, query)[:MAX_QUERY_TOKENS]
        room = MAX_TOKENS - len(query_ids) - 4
        scores: list[float] = []
        for start in range(0, len(texts), BATCH):
            rows = [
                [BOS_ID, *query_ids, EOS_ID, EOS_ID, *self._ids(spm, t)[:room], EOS_ID]
                for t in texts[start : start + BATCH]
            ]
            width = max(map(len, rows))
            ids = np.full((len(rows), width), PAD_ID, dtype=np.int64)
            mask = np.zeros((len(rows), width), dtype=np.int64)
            for i, row in enumerate(rows):
                ids[i, : len(row)] = row
                mask[i, : len(row)] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            scores.extend(float(x) for x in session.run(None, feed)[0][:, 0])
        self._last_used = time.monotonic()
        return scores

    def unload_if_idle(self):
        with self._lock:
            if self._session is not None and time.monotonic() - self._last_used > config.IDLE_TTL:
                log.info("unloading idle reranker")
                self._session = self._spm = None


reranker = CrossEncoder()


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _shingles(text: str) -> set[int]:
    tokens = TOKEN.findall(text.lower())
    if len(tokens) < SHINGLE:
        return {hash(" ".join(tokens))}
    return {hash(" ".join(tokens[i : i + SHINGLE])) for i in range(len(tokens) - SHINGLE + 1)}


def dedup(chunks: list[Chunk]) -> list[Chunk]:
    """Drop reprints and near-duplicate chunks, keeping the earliest copy.

    A source is a reprint when REPRINT of its word 5-grams already appear in earlier sources
    (same story, different header and footer); a chunk is a near duplicate when its
    Jaccard similarity with a kept chunk is at least NEAR_DUP.
    """
    shingles = [_shingles(c.text) for c in chunks]
    by_source: dict[int, set[int]] = {}
    for c, sh in zip(chunks, shingles, strict=True):
        by_source.setdefault(c.source, set()).update(sh)
    earlier: set[int] = set()
    reprints = set()
    for source, sh in by_source.items():
        if len(sh & earlier) >= REPRINT * len(sh):
            reprints.add(source)
        else:
            earlier |= sh

    kept: list[set[int]] = []
    index: dict[int, list[int]] = {}
    unique: list[Chunk] = []
    for c, sh in zip(chunks, shingles, strict=True):
        if c.source in reprints:
            continue
        shared = Counter(k for s in sh for k in index.get(s, ()))
        if any(n / (len(sh) + len(kept[k]) - n) >= NEAR_DUP for k, n in shared.items()):
            continue
        for s in sh:
            index.setdefault(s, []).append(len(kept))
        kept.append(sh)
        unique.append(c)
    return unique


def rank_chunks(
    query: str, chunks: list[Chunk], per_source: int, use_model: bool
) -> list[tuple[Chunk, float]]:
    """Return chunks with relevance in [0, 1], best first.

    BM25 keeps the top `per_source` candidates of every source; the cross-encoder
    (if enabled) rescores only those candidates.
    """
    if not chunks:
        return []
    lexical = bm25(query, [f"{c.heading} {c.text}" for c in chunks])
    by_source: dict[int, list[int]] = {}
    for i in sorted(range(len(chunks)), key=lambda i: -lexical[i]):
        by_source.setdefault(chunks[i].source, [])
        if len(by_source[chunks[i].source]) < per_source:
            by_source[chunks[i].source].append(i)
    candidates = sorted(i for ids in by_source.values() for i in ids)
    if use_model:
        texts = [f"{chunks[i].heading}\n{chunks[i].text}".strip() for i in candidates]
        scores = [_sigmoid(s) for s in reranker.score(query, texts)]
    else:
        top = max(lexical) or 1.0
        scores = [lexical[i] / top for i in candidates]
    ranked = sorted(zip(candidates, scores, strict=True), key=lambda p: -p[1])
    return [(chunks[i], s) for i, s in ranked]
