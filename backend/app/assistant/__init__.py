"""Conversational Ask: storage, the answer turn, and (from AI3) the tool loop.

Split by concern so the read-only mailbox rules stay auditable:
``store`` owns persistence, ``turn`` owns one grounded answer, ``tools`` owns
the bounded read-only tool loop.
"""