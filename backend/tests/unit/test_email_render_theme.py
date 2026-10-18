"""How the sandboxed email document is themed.

The reading pane frames this document with an empty ``sandbox``, which makes it
an opaque origin: it cannot see the app's ``data-theme``, it cannot inherit a
font, and it cannot inherit a colour scheme either. Everything it knows about
the reader's chosen theme arrives in this response, which is why it is a whole
document rather than a stylesheet the parent injects.

The central decision here is what to do with an email that brings its own
colours. Overriding them destroys the sender's design; keeping them on the app's
dark surface is unreadable. Both are worse than showing the message on paper in
either theme and saying so.
"""

import pytest

from app.api.routes_mail import (
    PAPER_PALETTES,
    THEME_PALETTES,
    _render_palette,
    _render_style,
)
from app.mail.sanitize import carries_inline_colour, declares_dark_background, sanitize_email_html


def themed(theme: str, *, paper: bool = False, dark_background: bool = False) -> str:
    return _render_style(
        _render_palette(theme, paper=paper, dark_background=dark_background),
        force_inherit=not paper,
    )


def path_for(html: str) -> tuple[str, str]:
    """The render path an email takes: ('themed'|'paper'|'darkfix', rendered css)."""
    safe = sanitize_email_html(html)
    dark = declares_dark_background(safe)
    paper = carries_inline_colour(safe) and not dark
    return ("darkfix" if dark else ("paper" if paper else "themed")), _render_style(
        _render_palette("dark", paper=paper, dark_background=dark), force_inherit=not paper
    )


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "html",
    [
        '<p style="color:#000000">Text</p>',
        '<td style="background-color:#ffffff">Cell</td>',
        '<div style="background: white">Cell</div>',
        '<table bgcolor="#ffffff"><tr><td>Cell</td></tr></table>',
        '<font color="red">Text</font>',
        '<body text="#333333">Text</body>',
    ],
)
def test_inline_colour_is_detected(html):
    assert carries_inline_colour(html) is True


@pytest.mark.parametrize(
    "html",
    [
        "",
        None,
        "<p>Plain prose with no markup opinions.</p>",
        "<p>Plain</p><a href='https://example.test'>link</a>",
        '<p style="font-weight:bold;padding:4px">Layout only</p>',
        '<img src="https://cdn.example.test/a.png" width="10">',
    ],
)
def test_colourless_markup_is_not_flagged(html):
    assert carries_inline_colour(html) is False


def test_a_stylesheet_based_colour_cannot_survive_sanitisation():
    """<style> is dropped with its content, so it can never reach the reader and
    must not trigger the paper path for content that will never be seen."""
    from app.mail.sanitize import sanitize_email_html

    sanitised = sanitize_email_html(
        "<html><head><style>p{color:red}</style></head><body><p>Text</p></body></html>"
    )

    assert "color:red" not in sanitised
    assert carries_inline_colour(sanitised) is False


# --------------------------------------------------------------------------- #
# the themed sheet
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_themed_sheet_takes_its_colours_from_the_app_tokens(theme):
    style = themed(theme)
    palette = THEME_PALETTES[theme]

    assert f"background:{palette['surface']}" in style
    assert f"color:{palette['ink']}" in style
    assert f"a{{color:{palette['link']}}}" in style


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_themed_sheet_pins_font_family_and_line_height(theme):
    """An opaque-origin frame inherits nothing, so an unnamed font silently
    falls back to the user-agent default serif."""
    style = themed(theme)

    assert "font-family:'IBM Plex Mono'" in style
    assert "line-height:1.6" in style


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_themed_sheet_forces_colour_onto_markup_with_none_of_its_own(theme):
    """A body rule loses to any inline style, and some clients supply a dark
    default. For a message with no colours of its own there is nothing to lose,
    so the theme wins outright."""
    assert "body *{color:inherit!important" in themed(theme)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_colour_override_never_covers_the_body_element_itself(theme):
    """This is the black-on-black bug, and it is a selector mistake.

    `*` matches <body> too, so `*{color:inherit!important}` makes the body
    inherit from <html> -- which carries no colour of its own -- and every
    message then renders in the user-agent default on our dark surface. The
    override has to be scoped to the body's descendants.
    """
    style = themed(theme)
    assert "*{color:inherit" not in style.replace("body *{color:inherit", "")
    body_rule = style.split("}")[0]
    assert f"color:{_render_palette(theme)['ink']}" in body_rule


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_selection_is_visible_in_both_themes(theme):
    palette = THEME_PALETTES[theme]
    style = themed(theme)

    assert "::selection{" in style
    assert f"background:{palette['select_bg']}" in style
    assert f"color:{palette['select_ink']}" in style
    assert len({THEME_PALETTES["light"]["select_bg"], THEME_PALETTES["dark"]["select_bg"]}) == 2


def test_the_selection_pair_is_itself_contrasty():
    def lum(hex_colour: str) -> float:
        raw = hex_colour.lstrip("#")
        channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def contrast(first: str, second: str) -> float:
        high, low = sorted((lum(first), lum(second)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    for theme in ("light", "dark"):
        palette = THEME_PALETTES[theme]
        assert contrast(palette["select_bg"], palette["select_ink"]) >= 4.5, theme
        assert contrast(palette["ink"], palette["surface"]) >= 4.5, theme


# --------------------------------------------------------------------------- #
# the paper path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_an_email_with_its_own_colours_renders_on_paper_in_both_themes(theme):
    style = themed(theme, paper=True)

    assert f"background:{PAPER_PALETTES['surface']}" in style
    assert f"color:{PAPER_PALETTES['ink']}" in style
    assert style == themed("light", paper=True), (
        "the paper sheet must not vary with the app theme"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_paper_sheet_never_overrides_the_senders_own_colours(theme):
    assert "color:inherit!important" not in themed(theme, paper=True)
    assert "font-family:inherit!important" not in themed(theme, paper=True)


def test_the_themed_sheet_does_not_leave_a_dark_email_unreadable():
    """Guards the regression directly: paper mode for a dark-background email
    paints white behind a dark table that covers it, leaving black ink on a black
    table. The dark path is chosen instead, and it does override."""
    assert "color:inherit!important" not in themed("dark", paper=True)
    assert "color:inherit!important" in themed("dark", dark_background=True)


def test_paper_ink_on_paper_meets_contrast_in_both_themes():
    assert PAPER_PALETTES["ink"] != THEME_PALETTES["dark"]["surface"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_paper_label_is_styled_to_be_readable_on_paper(theme):
    style = themed(theme, paper=True)
    palette = PAPER_PALETTES

    assert ".original-formatting{" in style
    assert f"background:{palette['muted']}" in style
    assert f"color:{palette['surface']}" in style


# --------------------------------------------------------------------------- #
# a dark email is the mirror image of a paper one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "html",
    [
        '<table bgcolor="#141419"><tr><td style="color:#000">Hi</td></tr></table>',
        '<div style="background:#101014">Hi</div>',
        '<div style="background-color: rgb(20,20,25)">Hi</div>',
        '<div style="background: navy">Hi</div>',
        '<td bgcolor="#000000">Hi</td>',
    ],
)
def test_a_dark_background_is_detected(html):
    assert declares_dark_background(html) is True


@pytest.mark.parametrize(
    "html",
    [
        "",
        None,
        "<p>No colours at all.</p>",
        '<table bgcolor="#ffffff"><tr><td>Hi</td></tr></table>',
        '<div style="background:#f8f7f7">Hi</div>',
        '<div style="background:transparent">Hi</div>',
        '<p style="font-weight:bold">Hi</p>',
    ],
)
def test_a_light_or_absent_background_is_not_dark(html):
    assert declares_dark_background(html) is False


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_dark_email_gets_light_ink_in_both_themes(theme):
    """This is the black-on-black bug.

    A sender-designed dark email keeps its background whatever the app does, so
    the ink is the only thing that can be fixed -- and it has to be light in the
    light theme too, or the app's own theme would be what makes it unreadable.
    """
    path, style = path_for('<div style="background:#141419;color:#000">Hi</div>')

    assert path == "darkfix"
    assert "color:inherit!important" in style
    palette = _render_palette(theme, dark_background=True)
    assert f"color:{palette['ink']}" in style
    assert palette["ink"] == THEME_PALETTES["dark"]["ink"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_dark_email_also_gets_a_dark_sheet(theme):
    """Forcing light ink while leaving the light theme's background in place is
    the same unreadable outcome by another route: the parts of the message
    outside the sender's own dark element would be light on light."""
    palette = _render_palette(theme, dark_background=True)

    assert palette["surface"] == THEME_PALETTES["dark"]["surface"]
    # Paper and dark are the two mirror images of each other, and neither of
    # them follows the app's theme.
    assert _render_palette(theme, paper=True)["surface"] != palette["surface"]


def test_a_dark_email_is_not_mistaken_for_a_paper_one():
    """Paper mode paints white behind the sender's table, which then covers it
    anyway -- so taking the paper path for a dark email is what produced black
    text on a black background in the first place."""
    dark_path, _ = path_for('<div style="background:#141419;color:#000">Hi</div>')
    paper_path, _ = path_for('<table bgcolor="#ffffff"><td style="color:#000">Hi</td></table>')

    assert dark_path == "darkfix"
    assert paper_path == "paper"
    assert dark_path != paper_path


def test_the_label_class_is_always_styled_even_where_it_is_not_used():
    """The sheet is shared by all three paths, so the rule exists even when no
    label is emitted. This is pinned so a future change that styles the label
    only on the paper path does not silently break the dark one."""
    for path in ("themed", "paper", "darkfix"):
        _, style = path_for(
            {
                "themed": "<p>plain</p>",
                "paper": '<table bgcolor="#ffffff"><td>x</td></table>',
                "darkfix": '<div style="background:#141419">x</div>',
            }[path]
        )
        assert ".original-formatting{" in style, path