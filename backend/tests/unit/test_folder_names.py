"""Folder naming: a reader never sees a storage key.

Outlook addresses a folder two ways -- a well-known key and a Graph id -- and
both reach the UI. Wherever a folder appears, the same name has to come out,
because a chip reading "Folder inbox" beside a sidebar reading "Inbox" reads as
two different folders.
"""

import pytest

from app.mail.folders import FOLDER_DISPLAY_NAMES, FOLDER_ORDER, folder_display_name
from app.retrieval.operator_search import reasons_for
from app.retrieval.operators import describe, parse_operators


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("inbox", "Inbox"),
        ("INBOX", "Inbox"),
        ("  sentitems  ", "Sent Items"),
        ("deleteditems", "Deleted Items"),
        ("junkemail", "Junk Email"),
    ],
)
def test_well_known_aliases_get_their_own_name(alias, expected):
    assert folder_display_name(alias) == expected


def test_a_custom_folder_keeps_something_the_reader_can_recognise():
    """Custom folders are synced by Graph id, so there is no mapping to apply.
    Title-casing the id is still better than a 40-character opaque string."""
    assert folder_display_name("project-apollo") == "Project-Apollo"
    assert folder_display_name("") == "Mail"


def test_the_ordering_list_covers_every_named_folder():
    assert set(FOLDER_ORDER) == set(FOLDER_DISPLAY_NAMES)


def test_a_folder_chip_is_labelled_with_the_name_not_the_alias():
    chips = describe(parse_operators("folder:sentitems"))

    assert [(chip["kind"], chip["label"], chip["value"]) for chip in chips] == [
        # The chip is labelled with the name; the value stays the raw alias,
        # because that is what removing the chip has to strip from the query.
        ("folder", "Folder Sent Items", "sentitems"),
    ]


def test_a_folder_reason_chip_is_labelled_with_the_name_not_the_alias():
    from types import SimpleNamespace

    row = SimpleNamespace(
        subject="Launch notes",
        sender_name="Morgan",
        sender_address="morgan@example.test",
        has_attachments=False,
        is_read=True,
        is_flagged=False,
    )

    reasons = reasons_for(row, parse_operators("folder:inbox"))

    assert reasons == [{"type": "folder", "detail": "In Inbox"}]


def test_removing_a_folder_chip_still_works_from_its_alias():
    """The label changed; the thing the frontend strips out of the query did not.
    These two are coupled and this is the test that keeps them coupled."""
    chip = describe(parse_operators("folder:deleteditems"))[0]

    assert chip["label"] == "Folder Deleted Items"
    assert "folder:deleteditems" in f"{chip['kind']}:{chip['value']}"