"""Text selection has to be a flat colour, and it has to be legible.

The regression this guards is easy to reintroduce and invisible in review: a
rule that sets a *gradient* on ::selection. Chromium accepts only a <color>
there and silently drops everything else, so the selection ends up with no
background at all while the stylesheet still looks correct -- and the paired
text colour then renders as white-on-white or black-on-black.
"""

from app.api.routes_mail import THEME_PALETTES, _render_style


def test_email_selection_uses_a_flat_colour_not_a_gradient() -> None:
    for theme in ("light", "dark"):
        css = _render_style(THEME_PALETTES[theme], force_inherit=True)
        rule = css.split("::selection{", 1)[1].split("}", 1)[0]
        assert "gradient" not in rule, f"{theme}: a gradient here renders as no background at all"
        assert rule.startswith("background:#"), f"{theme}: selection background is not a hex colour"
        assert "url(" not in rule, f"{theme}: an image is also not honoured in ::selection"


def test_email_selection_is_declared_for_firefox_too() -> None:
    css = _render_style(THEME_PALETTES["light"], force_inherit=True)
    assert "::-moz-selection{" in css, "Firefox honours only the prefixed form"


def test_both_themes_have_distinct_selection_colours() -> None:
    """Light and dark must not resolve to the same pair.

    If both themes picked the same ink, one of them would be unreadable no
    matter which pair was chosen.
    """
    light = (THEME_PALETTES["light"]["select_bg"], THEME_PALETTES["light"]["select_ink"])
    dark = (THEME_PALETTES["dark"]["select_bg"], THEME_PALETTES["dark"]["select_ink"])
    assert light != dark
    assert light[0] != light[1], "selection ink must contrast with its own background"
    assert dark[0] != dark[1]