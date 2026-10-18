# Changelog

## Unreleased

### Added

- **Conversational Ask.** Chats are listed, renamed, pinned, archived, searched and deleted from a history rail; the transcript and the cited email sit beside it in three independently scrolling panes; and a temporary chat answers from the same grounded retrieval while leaving nothing in the database.
- **Follow-ups work.** Each turn is grounded in the earlier ones and in the messages already under discussion, so "what about the second one?" resolves. The working set stores email *ids* only and bodies are re-read from `emails`, so a deleted email stops resolving instead of lingering in a chat.
- **Long chats stay bounded.** Older turns fold into a running summary every twelve messages, keeping the prompt small without deleting what the reader can scroll. Folding is best effort: a provider failure leaves the transcript intact and the turn still answers.
- **Bounded read-only tools.** The assistant can now choose what to read -- `search_emails`, `read_email`, `list_recent`, `count_emails`. This is the one place it gets to pick, and the constraints are enforced in code rather than trusted to the prompt: there is no write tool and no Graph call; no tool accepts a `user_id` or `mailbox_id` and every query is scoped to the user and the conversation's own mailbox; and steps, rows per call, body size and total emails read per turn are all capped. When the budget runs out the loop stops and answers from what it had -- it never asks the model to continue. Grounding is unchanged: the final answer still goes through `verify_answer`, so a tool cannot introduce an unsupported claim. A provider that cannot drive tools degrades to single-turn retrieval rather than failing.
- **Stated memory.** "Always give me the dates" is kept as a preference and injected into later answers. Nothing is inferred from what is read, every note is listed in Settings in the reader's own words, and each can be removed there. Notes are deleted with the mailbox they came from.
- **Mailbox routing.** With several mailboxes, a question picks one from the conversation's own mailbox, then an address named in the question, then a remembered preference, then the selection. The model is never asked to choose, because a mis-route is invisible to the reader.
- **Follow-up suggestions.** Up to three questions this reader might ask next, drawn from the conversation and the subject lines already retrieved -- never from mail the reader has not been shown. They are buttons that fill the composer and nothing is sent until they press Send: an offer, not a prefill. A provider failure returns an empty list.

### Fixed

- **The composer locked after one message, and every reply appeared twice.** Both came from one omission: the turn's `done` event had no handler on the Ask page. Without it `sending` was never cleared, so the textarea and Send button stayed disabled for the rest of the session; and the streaming bubble was never taken down, so once the transcript reloaded the same answer was on screen twice -- once with its cited emails, then again as a bare bubble. The handler now ends the turn, and the final `answer` event replaces the streamed text rather than being appended to it.
- **The tool loop could never run.** `app/assistant/tool_loop.py` asks the client for the next tool call, but no client implemented it -- `ClaudeClient` and `GroqClient` only had `json_message`. Every turn raised `ToolsUnavailable` and fell back to single-turn retrieval, so AI3's safety layer guarded code that never executed. Both clients now drive native tool calling (Anthropic tool use; OpenAI-compatible function calling), with usage accounted per turn.
- **A turn with no answer showed an empty bubble.** The result carries an `explanation` when retrieval finds nothing, but only `answer` was stored and streamed, so the reader saw an empty message with no reason given. The explanation is now shown and stored.
- **The chat layout overlapped itself at 390px.** The mobile rail was sized with `max-height` inside an `auto` grid row, so it overflowed and the history card sat on top of the transcript. The rows are sized explicitly instead of their contents.
- **A conversational question matched nothing.** `parse_query` keeps every token, which is right for a search box but not for a question: "what did they say about the budget?" is seven words and one topic, and requiring all seven matched no message at all. Every conversational ask therefore fell back to an empty keyword list, and the degraded path the spec relies on returned nothing instead of the reader's matches. The ask path now drops conversational filler before searching, and the stop-word list gained the pronouns and copulas it was missing (`they`, `he`, `she`, `its`, `our`, `us`, `being`, `should`, ...).
- A provider that is unreachable or misconfigured no longer fails the ask request: the client factory's failures are caught alongside the spend-cap refusal, so the reader gets keyword results instead of a 500.
- `test_retrieval_ranks_by_relevance_not_by_date` compared an email's UUID against the excerpt *labels* (`E1`, `E2`, ...), which can never match. It now reads the ranking from the subjects in the order they were offered to the model, which is what the test's name claims to check.

### Measured

Against the 5,004-message demo mailbox (`demo@example.test`) with the configured provider, five conversational turns (`eval/measure_chat.py`, `eval/result-chat.json`):

| | median | range |
|---|---|---|
| Time to first token | 6.29s | 4.76 – 7.07s |
| Total turn | 6.29s | — |
| Tool steps per turn | 2 | 0 – 3 |
| Provider calls per turn | 3 | — |
| Tokens per turn | 542 | — |

Every call count is the real provider's, not an estimate: the loop reports `calls`, `input_tokens` and `output_tokens` on each stored turn. Turn cost is dominated by the tool round-trips -- 2 tool steps plus the final verified answer is 3 calls, against 2 for the single-turn path. The `CLAUDE_DAILY_CALL_CAP` budget therefore goes roughly 1.5x faster per conversational turn than per ask, which is worth revisiting before the cap is enabled for chat.

### Changed

- `docs/ARCHITECTURE.md` now states where per-user isolation actually comes from. The app connects as a superuser in both shipped databases, which bypasses RLS even when it is forced, so the explicit `user_id` predicate is currently the only layer doing the work. The file carries the exact statements to make the policies real.

- The mail screen no longer scrolls as a page. It is an application shell now: the document is exactly the viewport tall, and the folders, message list and reading pane are three independent scroll containers. Previously the page scrolled underneath all three panes, so one wheel gesture moved the list, the reading pane and the document together.
- The message list's infinite scroll observes the list container instead of the window. With the shell no longer scrolling, a viewport-rooted observer never fired after the first page, so paging simply stopped working.
- The quick-filter row and the date headers are sticky inside the list rather than sitting above it, and the reading pane remembers its scroll position per message and starts a new message at the top.
- The sync status line is pinned to the bottom of the visible folder pane. It sat directly under the last folder, so it scrolled out of sight as soon as there were more folders than fit.
- Collapsing the folder pane is now a 56px rail that animates over 150ms and hands its width to the list and the reading pane. The grid columns were percentages, which are resolved against the grid width and so could never take up the freed space. The collapsed state is remembered in `localStorage` and the rail is the default between 768px and 1100px wide.
- Every folder has a distinct icon, a tooltip, an accessible name and a readable unread badge in the rail, and folders with no indexed mail are no longer offered as dead ends.
- The folder pane's collapse control is always visible, flips its chevron, and carries `aria-expanded` and a matching label.
- Email bodies are themed from the app's own tokens -- colour, background, link colour, font family, line height and the matching `color-scheme` -- and are re-sent whenever the theme changes.
- Email text is no longer black on a black background in dark theme. The rule that forces a message's colour onto the theme used a universal selector, which also matches the `<body>` element itself; the body therefore inherited from `<html>`, which carries no colour, and every message fell back to the user-agent default. It is now scoped to the body's descendants.
- An email that brings its own colours is no longer force-repainted into the app's palette. Forcing it destroyed the sender's design in one theme and left black-on-black in the other; such a message is now shown on paper in both themes with an "Original formatting" label, and only colourless and plain-text messages inherit the theme.
- A sender-designed *dark* email gets the mirror-image treatment: a dark sheet with light ink in both themes. Its background paints over ours whatever we do, so replacing the ink is the only thing that can keep it readable -- and it has to be replaced with light ink even for a light-theme reader.
- Text selection is legible inside the email frame in both themes.
- `--text-3` was darkened in the light theme. It passed on the page and panel backgrounds but not on the brand-tinted selected row, where timestamps, previews and attachment marks scored 4.21:1. A contrast check across the mail screen in both themes, expanded and collapsed, now passes at 4.5:1 or better.

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
