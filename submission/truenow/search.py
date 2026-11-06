"""A small BM25 index. Results are ordered deterministically (score, then position)."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .text import tokens

_K1 = 1.2
_B = 0.6


class Index:
    def __init__(self, documents: list):
        self.size = len(documents)
        self.lengths = []
        self.postings = defaultdict(list)
        for position, document in enumerate(documents):
            words = tokens(document)
            self.lengths.append(len(words))
            for word, count in Counter(words).items():
                self.postings[word].append((position, count))
        self.average = (sum(self.lengths) / self.size) if self.size else 0.0

    def idf(self, word: str) -> float:
        n = len(self.postings.get(word, ()))
        return math.log(1 + (self.size - n + 0.5) / (n + 0.5))

    def search(self, query_words: list, limit: int = 60) -> list:
        scores = defaultdict(float)
        for word in set(query_words):
            postings = self.postings.get(word)
            if not postings:
                continue
            weight = self.idf(word)
            for position, count in postings:
                norm = 1 - _B + _B * self.lengths[position] / (self.average or 1)
                scores[position] += weight * count * (_K1 + 1) / (count + _K1 * norm)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:limit]
