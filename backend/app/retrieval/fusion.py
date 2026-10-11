from collections import defaultdict


def reciprocal_rank_fusion(lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranked in lists:
        for rank, identifier in enumerate(ranked, start=1):
            scores[identifier] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
