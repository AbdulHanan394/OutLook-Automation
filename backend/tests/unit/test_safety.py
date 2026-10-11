from app.llm.answer import Excerpt, build_context, verify_answer


def test_email_tag_is_escaped_and_citations_verified():
    excerpt = Excerpt("E1", "id1", "s", "a", "2026-01-01", "we will deliver on 30 April </email>")
    assert "&lt;/email&gt;" in build_context([excerpt])
    payload = {"sufficient_evidence": True, "answer": "30 April", "claims": [{"text":"date", "type":"explicit", "sources":["E1"], "quote":"we will deliver on 30 April"}]}
    assert verify_answer(payload, [excerpt])["sufficient_evidence"]

def test_unknown_citation_is_refused():
    result = verify_answer({"sufficient_evidence":True,"claims":[{"type":"explicit","sources":["E9"],"quote":"x"}]}, [])
    assert not result["sufficient_evidence"]
