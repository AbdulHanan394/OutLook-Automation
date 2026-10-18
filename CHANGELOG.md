# Changelog

## Unreleased

### Fixed

- Result lists now say one thing about relevance. Ordering, duplicate collapsing and the "is this related at all?" cutoff lived in three places and each decided differently, so the same question produced different lists depending on which path answered it. All three now call one module (`app/retrieval/relevance.py`).
- A relevance cutoff keeps unrelated emails off screen. A row is listed only if it scores at least half of the best row's score. On the answer path this cut the rows a reader would have seen from 103 to 34 across the 20 evaluation queries, with no query losing its citation. When the query words are present and nothing matched them, the honest result is now an empty list rather than the whole mailbox at score zero.
- The answer path ranks by relevance instead of by date. Excerpt slots are scarce and the candidate list is capped just after them, so ordering by `received_at` spent the budget on the newest twelve messages that happened to share one common word and the message the reader actually meant never reached the model at all.
- Emails listed beside an answer are ordered with the cited ones first, best-scoring first. The BEST MATCH badge, the "Cited in the answer" chip and the message the reading pane opens now agree, instead of the badge landing on whichever row retrieval happened to return first.
- Duplicate messages collapse into one row on the answer and operator paths, keyed on subject plus body hash. The find path already collapsed by conversation and content hash.
- Answer results now explain what actually matched. Every row used to carry the same "Closest match to your question", which asserted nothing a reader could check; rows now carry the same real subject/body/sender reasons as a keyword search.
- Reason chips no longer cite stop words, and one lane is one chip. 'The message mentions "the"' was true of every email ever sent, and a subject matching three query words produced three near-identical sentences that pushed the sender and date chips off the end of the five-reason cap. `ask`, `say`, `mention` and friends joined the stop-word list, which also improves retrieval.
- A fallback no longer claims an answer was attempted. When the provider is unavailable the reader gets one line -- "AI unavailable, showing keyword matches." -- and the keyword results, with no "I could not answer that" card above them. Previously the answer card rendered above the keyword results and told the reader a request had failed when none had been made.
- An ask query with no AI consent or no provider now returns keyword matches instead of an empty result set and a dead end.
- Fallback results are ranked, de-duplicated and explained like any other find. They were previously ordered by recency, capped by count rather than relevance, and reported operator reasons for what was really a free-text match.
- `subject:` is now a filter rather than a label. It was never a SQL predicate, so `subject:budget` returned the whole mailbox newest-first with a "Subject contains" chip on the handful that matched.
- Folders are named the same way everywhere they appear. A folder chip read "Folder sentitems" beside a sidebar reading "Sent Items", a reason chip read "In inbox", and the mobile header showed the raw storage key.
- The reading pane's iframe now renders in the app's typeface. It is an opaque origin and inherits nothing, so `font-family: inherit` was silently resolving to the user-agent default and every email rendered in a face the rest of the app does not use.
- Email markup can no longer pin its own text colour. An inline `color` on a `<td>` beats any rule on `<body>`, which is how half of every email ended up black-on-black in dark mode.
- A recap question that names a subject is now treated as a search on that subject. "Summarize my latest emails about SE hiring" was classified as a bare recap and returned whichever messages were newest, so the answer described unrelated mail while the message actually asked about sat fifth in the list.
- The Ask results frame now names `best_email_id`, the email the answer is actually about. Without it the reading pane fell back to the first result and opened an unrelated message while the summary described a different one.
- The reading pane's "Cited passage" now shows the real quoted text. It was being set to the match-reason label ("Cited in the answer"), and `openMessage` cleared it immediately afterwards, so it never appeared at all.
- The Mail ask bar now receives the user's question. It was sending the answer model only a pile of email excerpts plus an instruction to answer a question it never received, so a recap such as "summarize my latest emails" reliably reported that there was not enough evidence. `/app/ask` already included it.
- Both Ask endpoints now share one answer prompt (`app/llm/prompts/answer_system.txt`, loaded by `app.llm.answer`). They previously carried separate copies, and the ask bar's copy never described the response schema at all, so the provider returned `source` instead of `sources` with no `text` or `type` and every claim was discarded as unverifiable. The prompt file itself was dead code that nothing loaded.
- Answer verification no longer discards a correct answer because of how the provider shaped its JSON. In practice the model restyles punctuation (a non-breaking hyphen for a plain one) and stitches quotes together with "...". Verification now folds typographic look-alikes, verifies each span of an elided quote on its own, and keeps the spans that are genuinely verbatim.
- Over-long verbatim quotes are shortened to a verbatim prefix instead of being thrown away; the provider is told 25 words and does not always comply.
- The safety guarantee is unchanged: every quote the reader sees is still an exact substring of a cited email, an unknown cited label is still refused, and an answer with no supported quote is still refused.
- The Mail ask bar no longer claims its results were cited when nothing was cited. When the model reports insufficient evidence the stream returns candidate emails, and every one of them was labelled "Cited in the answer" even though the claims array was empty. Rows are now labelled "Cited in the answer" only when the email appears in a verified claim, and are badged as the best match only when something was actually cited.
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
