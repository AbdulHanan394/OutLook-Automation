from dataclasses import dataclass
from typing import Any

from app.retrieval.parse import Criteria, parse_query


@dataclass(frozen=True)
class SearchClues:
    rewrites: list[str]
    soft_clues: list[str]


def extract_search_clues(payload: dict[str, Any]) -> SearchClues:
    allowed = {"rewrites", "soft_clues"}
    if set(payload) - allowed:
        raise ValueError("unexpected search clue fields")

    values: dict[str, list[str]] = {}
    for name in allowed:
        value = payload.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{name} must be a string array")
        clean = [" ".join(item.split())[:160] for item in value if item.strip()]
        values[name] = list(dict.fromkeys(clean))[:8]
    return SearchClues(rewrites=values["rewrites"], soft_clues=values["soft_clues"])


def add_soft_clues(criteria: Criteria, clues: SearchClues) -> Criteria:
    additions = parse_query(" ".join(clues.rewrites + clues.soft_clues))
    terms = list(dict.fromkeys(criteria.terms + additions.terms))[:30]
    phrases = list(dict.fromkeys(criteria.phrases + additions.phrases))[:12]
    return Criteria(
        query=criteria.query,
        terms=terms,
        phrases=phrases,
        sender=criteria.sender,
        from_dt=criteria.from_dt,
        to_dt=criteria.to_dt,
        folder=criteria.folder,
    )
