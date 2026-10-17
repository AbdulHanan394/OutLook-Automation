"""Regression tests for assistant full-text query construction.

The bug these guard against: ``plainto_tsquery`` ANDs every token it receives, so
passing a whole question ("tell me about the software hiring email") built a
conjunction no email could satisfy, while bare keywords ("software hiring
email") matched. Natural language was strictly worse than keywords.

These tests assert the *generated SQL shape* only. They cannot prove the SQL is
valid Postgres -- see ``test_fts_postgres.py`` for that, which executes against a
real database.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.db import EMAIL_SEARCH_VECTOR, Email
from app.retrieval.fts import (
    MAX_UNITS,
    build_match_filter,
    build_queries,
    build_rank_query,
    query_units,
)


def render(units):
    """Compile the real retrieval query for a set of units."""
    queries = build_queries(units)
    stmt = (
        select(Email.subject)
        .where(build_match_filter(EMAIL_SEARCH_VECTOR, queries))
        .order_by(func.ts_rank(EMAIL_SEARCH_VECTOR, build_rank_query(queries)).desc())
    )
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


def where_clause(units):
    """Just the WHERE clause -- 'ORDER' itself contains the substring 'OR'."""
    return render(units).split("WHERE", 1)[1].split("ORDER BY", 1)[0]


def test_question_filler_words_are_not_search_terms():
    assert query_units("tell me about the software hiring email", "") == ["software", "hiring"]


def test_bare_keywords_and_full_sentence_reduce_to_the_same_units():
    """The original symptom: these two questions must be equally searchable."""
    assert query_units("software hiring email", "") == query_units(
        "tell me about the software hiring email", ""
    )


def test_ai_keywords_are_merged_not_conjuncted():
    units = query_units("what did we decide about the budget?", "budget approval q4")
    assert units == ["decide", "budget", "approval", "q4"]


def test_quoted_phrases_are_preserved_intact():
    assert "launch date" in query_units('what about the "launch date" agreement?', "")


def test_units_are_deduplicated_and_capped():
    assert query_units("software software software", "software") == ["software"]
    assert len(query_units(" ".join(f"term{i}" for i in range(100)), "")) == MAX_UNITS


def test_stopword_only_question_yields_no_query():
    assert query_units("the a an of it is", "") == []
    assert build_match_filter(EMAIL_SEARCH_VECTOR, []) is None
    assert build_rank_query([]) is None


def test_units_are_ored_as_booleans_not_as_tsquery_values():
    """Postgres cannot OR two tsquery values; the boolean results must be OR-ed."""
    where = where_clause(["software", "hiring"])
    # One @@ application per unit (each returning boolean), joined by a single OR.
    assert where.count("@@") == 2
    assert where.count(" OR ") == 1
    assert where.index("OR") > where.index("@@")


def test_single_unit_query_has_no_or():
    assert " OR " not in where_clause(["software"])


def test_rank_query_uses_the_tsquery_or_operator():
    """ts_rank needs one tsquery, combined with tsquery's own ``||`` operator."""
    order = render(["software", "hiring"]).split("ORDER BY", 1)[1]
    assert "ts_rank(emails.search_tsv," in order
    assert "||" in order
    # The boolean OR must not leak into the tsquery argument.
    assert " @@ " not in order


def test_retrieval_targets_the_gin_indexed_generated_column():
    """search_tsv is generated + GIN indexed; the old code recomputed a tsvector
    over email_chunks.chunk_text, which cannot use any index."""
    sql = render(["software"])
    assert "emails.search_tsv" in sql
    assert "to_tsvector" not in sql
    assert "email_chunks" not in sql