# Changelog

## Unreleased

### Fixed

- The Mail ask bar now receives the user's question. It was sending the answer model only a pile of email excerpts plus an instruction to answer a question it never received, so a recap such as "summarize my latest emails" reliably reported that there was not enough evidence. `/app/ask` already included it.
- Both Ask endpoints now share one answer prompt (`app/llm/prompts/answer_system.txt`, loaded by `app.llm.answer`). They previously carried separate copies, and the ask bar's copy never described the response schema at all, so the provider returned `source` instead of `sources` with no `text` or `type` and every claim was discarded as unverifiable. The prompt file itself was dead code that nothing loaded.
- Answer verification no longer discards a correct answer because of how the provider shaped its JSON. In practice the model restyles punctuation (a non-breaking hyphen for a plain one) and stitches quotes together with "...". Verification now folds typographic look-alikes, verifies each span of an elided quote on its own, and keeps the spans that are genuinely verbatim.
- Over-long verbatim quotes are shortened to a verbatim prefix instead of being thrown away; the provider is told 25 words and does not always comply.
- The safety guarantee is unchanged: every quote the reader sees is still an exact substring of a cited email, an unknown cited label is still refused, and an answer with no supported quote is still refused.
- The Mail ask bar no longer claims its results were cited when nothing was cited. When the model reports insufficient evidence the stream returns candidate emails, and every one of them was labelled "Cited in the answer" even though the claims array was empty. Rows are now labelled "Cited in the answer" only when the email appears in a verified claim, and "Closest match to your question" otherwise.
- The Mail ask bar no longer says "I could not find this" directly above a list of results. It now says "I could not answer that" whenever candidates are on screen, and the result list explains that none of them is a reliable match.
- "Summarize my latest emails" in the Mail ask bar no longer describes a single conversation. Its recap retrieval took the N most recent messages outright, so one long thread could occupy every excerpt slot; it now keeps one message per conversation, matching what `/app/ask` already did.

### Maintenance

- The lint and type gate is green again and enforced by CI. `ruff` now pins its rule set in `backend/pyproject.toml` (its defaults widened between 0.8 and 0.17, which had silently turned a passing gate red), and CI runs `ruff`/`mypy` from `backend/` so the project's strict mypy settings apply.
- `npm run lint` works: added `frontend/eslint.config.mjs` for ESLint 9 and a `typecheck` script. Both are run in CI.
- `Timestamped.updated_at` used the deprecated, timezone-naive `datetime.utcnow` on a `DateTime(timezone=True)` column; it now uses a timezone-aware value.
- The request-id middleware computed a duration it never used; it now logs the request duration through the existing redacting log filter.
- Narrowed two over-broad `except Exception` handlers (`routes_ask`, `crypto.valid_key`) and documented the three that are deliberate safety nets.
- `.gitignore` now covers `*.tsbuildinfo`, `frontend/test-results/`, `frontend/playwright-report/`, and `eval/.demo_session.json` (which holds a live session token). `frontend/tsconfig.tsbuildinfo` was untracked.
- Documented in the README that the Postgres integration tests truncate every table between tests and must be pointed at a scratch database, and that `DATABASE_URL` uses the compose-only hostname `db`.
- M11: Search results now include retrieval-lane match reasons and Postgres-highlighted preview snippets.
- M12: Find results can be rejected and refined with an optional hint, including relative "older than that" refinement.
- M13: Searches can be saved per Outlook mailbox and emails pinned; saved items are isolated by user and removed with their parent mailbox or email.
- M14: Optional per-user match ratings can break retrieval ties, expire after a configurable retention period, and be cleared from Settings.
- M15: AI-consented searches can use bounded semantic rewrites and soft clues while preserving explicit sender, date, and folder filters.
- M16: Search collapses duplicates by conversation or normalized content and lets users expand the related messages.
- M17: Expanded threads can be summarized on demand with verified source quotes and per-user, source-hash-invalidated caching.
- M18: Ask can identify timeline questions and present verified email-supported events ordered by Outlook message date.
- M19: Extracts sent-message commitments with verified quotes, deduplicated storage, due-date clues, source links, and done/dismiss actions.
- M20: Adds user-scoped contact message/thread counts, open commitments, and an on-demand cited Claude brief without external contact lookups.
- M21: Indexes text from supported Outlook attachments with bounded in-memory downloads and adds file-type filtering to Find.
