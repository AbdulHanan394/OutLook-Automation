from typing import Literal


def confidence(scores: list[float], *, min_score: float, margin: float, agreeing_retrievers: int) -> Literal["high", "low", "none"]:
    if not scores or scores[0] < min_score:
        return "none"
    gap = scores[0] - (scores[1] if len(scores) > 1 else 0)
    return "high" if gap >= margin and agreeing_retrievers >= 2 else "low"
