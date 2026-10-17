"""Full-text query construction for the assistant endpoint.

``plainto_tsquery`` ANDs every token it is given. Passing it a whole
natural-language question therefore produces a conjunction that no single email
can satisfy -- ``'tell' & 'me' & 'about' & 'softwar' & 'hir' & 'email'``. Asking
in plain English was strictly *worse* than asking for bare keywords, because the
conversational filler words became mandatory search terms.

The helpers here split free text into small independent units and OR them, so any
single unit is enough to retrieve a row. Quoted phrases stay conjunctive within
themselves, preserving exact-phrase intent.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, or_

from app.retrieval.parse import required_query_terms

MAX_UNITS = 24
MAX_UNIT_LENGTH = 60


def query_units(*texts: str | None) -> list[str]:
    """Turn one or more strings into de-duplicated, stopword-free search units.

    Quoted phrases are kept intact so they remain conjunctive; every other word
    becomes its own unit and therefore its own alternative. Conversational filler
    from :data:`app.retrieval.parse.SEARCH_STOP_WORDS` is dropped, so Find and
    Ask reduce the same sentence to the same terms.
    """
    units: list[str] = []
    for text in texts:
        if not text:
            continue
        terms, phrases = required_query_terms(text)
        for unit in (*phrases, *terms):
            normalised = " ".join(unit.split())[:MAX_UNIT_LENGTH]
            if normalised and normalised not in units:
                units.append(normalised)
    return units[:MAX_UNITS]


def build_queries(units: list[str]) -> list[ColumnElement]:
    """One ``plainto_tsquery`` per unit.

    Input still goes through ``plainto_tsquery``, so it stays stemmed,
    stopword-normalised and injection-safe, but each unit is independent rather
    than being AND-ed into one unsatisfiable conjunction.
    """
    return [func.plainto_tsquery("english", unit) for unit in units]


def build_match_filter(
    vector: ColumnElement, queries: list[ColumnElement]
) -> ColumnElement[bool] | None:
    """Match a row when *any* unit matches: ``tsv @@ q1 OR tsv @@ q2``.

    The boolean results are OR-ed, not the tsquery values. Postgres rejects
    ``q1 OR q2`` outright -- ``argument of OR must be type boolean, not type
    tsquery`` -- so OR-ing the queries directly is not an option.
    """
    clauses = [vector.op("@@")(query) for query in queries]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else or_(*clauses)


def build_rank_query(queries: list[ColumnElement]) -> ColumnElement | None:
    """Combine units into one tsquery for ``ts_rank``.

    Here ``||`` is the tsquery OR operator, so ``q1 || q2`` is a single valid
    tsquery that ranks rows matching more units higher.
    """
    if not queries:
        return None
    combined = queries[0]
    for query in queries[1:]:
        combined = combined.op("||")(query)
    return combined.self_group()