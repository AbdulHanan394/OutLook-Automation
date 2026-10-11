# Changelog

## Unreleased

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
