"""Human-readable names for mailbox folders.

Outlook addresses a folder two ways: a well-known key (``inbox``, ``sentitems``)
and a Graph id. Both end up in the ``emails`` table, and both can reach the UI --
in a sidebar row, in an Ask bar chip, in a "why this matched" reason. Showing the
reader ``AAMkAGI2...`` because that is what the database happened to store is a
leak of implementation detail into the only surface that matters.

Every display path goes through :func:`folder_display_name`, so a folder is named
the same way everywhere it appears.
"""

# Outlook's well-known folder names. These are the mailbox owner's own folder
# names, not Microsoft branding: they are the labels Graph itself returns for
# parentFolderId, and a mailbox in any locale gets these keys.
FOLDER_DISPLAY_NAMES = {
    "inbox": "Inbox",
    "sentitems": "Sent Items",
    "drafts": "Drafts",
    "archive": "Archive",
    "deleteditems": "Deleted Items",
    "junkemail": "Junk Email",
    "outbox": "Outbox",
    "outboxs": "Outbox",
    "recoverableitemsdeletions": "Recoverable Items",
}
FOLDER_ORDER = tuple(FOLDER_DISPLAY_NAMES)


def folder_display_name(alias: str) -> str:
    """The reader-facing name for a folder alias or Graph id."""
    key = alias.strip().lower()
    if key in FOLDER_DISPLAY_NAMES:
        return FOLDER_DISPLAY_NAMES[key]
    # A custom folder synced by its Graph id: fall back to the alias so the
    # sidebar still shows something the reader can distinguish.
    return alias.strip().title() or "Mail"