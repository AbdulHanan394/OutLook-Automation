from app.llm.questions import is_recent_summary_question


def test_recent_mail_summaries_are_not_treated_as_keyword_searches():
    assert is_recent_summary_question(
        "Give me a gentle summary of my recent conversations"
    )
    assert is_recent_summary_question("Can you summarize my latest emails?")
    assert not is_recent_summary_question("Summarize the launch plan")
    assert not is_recent_summary_question("What should I follow up on?")
