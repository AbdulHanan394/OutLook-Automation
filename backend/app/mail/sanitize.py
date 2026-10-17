"""Allowlist sanitiser for untrusted email HTML.

Email HTML is attacker-controlled data. Nothing that reaches the reading pane is
trusted, and nothing here is meant to be clever: the output is a small allowlist
of presentational tags with every active or outbound-fetching construct removed.

Two invariants this module must never lose:

1. The output must be inert on its own. It is rendered inside a sandboxed iframe
   with an empty ``sandbox`` attribute (no scripts, no same-origin, no forms, no
   top-level navigation) *and* served with ``default-src 'none'``, but the
   sanitiser is the layer that keeps the database free of hostile markup in the
   first place -- a stored XSS that never reaches a browser is better than one
   that is blocked at request time.
2. Remote resources never load unless the reader explicitly asks. Tracking
   pixels are the reason this function takes an ``allow_images`` flag: an
   ``<img>`` that points at a third-party host is a beacon that reports the
   mailbox was opened, so its ``src`` is dropped and turned into an inert
   placeholder by default.
"""

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import Comment, NavigableString

# Presentational tags that carry no scripting or fetch behaviour of their own.
ALLOWED_TAGS = frozenset({
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "blockquote",
    "br", "caption", "center", "cite", "code", "col", "colgroup", "dd", "del",
    "details", "dfn", "div", "dl", "dt", "em", "figcaption", "figure", "font",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "img",
    "ins", "kbd", "li", "main", "mark", "nav", "ol", "p", "pre", "q", "rp", "rt",
    "ruby", "s", "samp", "section", "small", "span", "strike", "strong", "sub",
    "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "time",
    "tr", "tt", "u", "ul", "var", "wbr",
})

# Tags whose *content* must go too, not just the tag itself.
DROP_WITH_CONTENT = frozenset({
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "applet", "form", "input", "button", "select", "option", "optgroup",
    "textarea", "template", "noscript", "svg", "math", "canvas", "audio",
    "video", "source", "track", "base", "meta", "link", "title", "head",
})

ALLOWED_ATTRIBUTES = frozenset({
    "href", "src", "title", "alt", "width", "height", "align", "valign",
    "colspan", "rowspan", "border", "cellpadding", "cellspacing", "dir",
    "lang", "start", "type", "datetime", "cite", "color", "face", "size",
    "bgcolor", "style",
})

# Schemes an href may use. Anything else (javascript:, data:, vbscript:, file:)
# is dropped outright rather than rewritten.
SAFE_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

MAX_HTML_LENGTH = 2_000_000
MAX_NESTING_DEPTH = 100
MAX_URL_LENGTH = 2_048

# Inline styles are preserved because email layout depends on them, but these
# three constructs turn a style into a fetch or an execution primitive.
_CSS_URL = re.compile(r"url\s*\(", re.IGNORECASE)
_CSS_EXPRESSION = re.compile(r"(expression|behaviou?r|-moz-binding)\s*[:(]", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import", re.IGNORECASE)
_EVENT_ATTR = re.compile(r"^on[a-z]+$", re.IGNORECASE)
_WHITESPACE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _is_safe_url(value: str | None) -> bool:
    if not value:
        return False
    candidate = value.strip()
    # Strip control characters and whitespace that browsers ignore when
    # resolving a scheme, so "java\nscript:" cannot slip past.
    cleaned = _WHITESPACE.sub("", candidate).lower()
    if not cleaned or cleaned.startswith(("#", "/")):
        return True
    try:
        scheme = urlsplit(candidate).scheme.lower()
    except ValueError:
        return False
    if not scheme:
        # A relative reference such as "image.png" has no scheme and cannot
        # execute. It still cannot resolve usefully inside the iframe, but it is
        # harmless.
        return True
    return scheme in SAFE_SCHEMES


def _clean_style(value: str | None) -> str | None:
    if not value:
        return None
    if _CSS_URL.search(value) or _CSS_EXPRESSION.search(value) or _CSS_IMPORT.search(value):
        # Removing only the offending declaration would leave something like
        # "color: ; background: red" behind, which is still valid CSS but is
        # noise the reader never asked for. An email that reaches for url() or
        # expression() is almost always a marketing template whose value here is
        # entirely its images and fonts, so the whole style is dropped.
        return None
    return _WHITESPACE.sub("", value)[:4_000]


def _clean_attributes(tag: Tag, *, allow_images: bool) -> None:
    for name in list(tag.attrs):
        lowered = name.lower()
        value = tag.attrs[name]

        if lowered in {"srcset", "ping", "formaction", "action", "background", "dynsrc", "lowsrc"}:
            del tag.attrs[name]
            continue
        if _EVENT_ATTR.match(lowered) or lowered.startswith("xlink:"):
            del tag.attrs[name]
            continue
        if lowered not in ALLOWED_ATTRIBUTES:
            del tag.attrs[name]
            continue

        if isinstance(value, list):  # bs4 splits class/style/rel into lists
            value = " ".join(value)

        if lowered == "style":
            cleaned = _clean_style(value)
            if cleaned:
                tag.attrs[name] = cleaned
            else:
                del tag.attrs[name]
            continue

        if lowered == "href":
            if not _is_safe_url(value):
                del tag.attrs[name]
                continue
            tag.attrs[name] = value.strip()[:MAX_URL_LENGTH]
            continue

        if lowered == "src":
            # Only <img> reaches this branch; every other src-bearing tag was
            # dropped with its content above.
            if not allow_images or not _is_safe_url(value):
                del tag.attrs[name]
                tag.attrs["data-blocked-image"] = "true"
            else:
                tag.attrs[name] = value.strip()[:MAX_URL_LENGTH]
            continue

        tag.attrs[name] = value[:1_024]


def _walk(node: Tag, depth: int, *, allow_images: bool) -> None:
    if depth > MAX_NESTING_DEPTH:
        node.replace_with(NavigableString(""))
        return

    for child in list(node.children):
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in DROP_WITH_CONTENT:
            child.extract()
            continue
        if child.name not in ALLOWED_TAGS:
            # Unknown tag: drop the element but keep the text inside it, so
            # <custom-block>Hello</custom-block> still reads as "Hello".
            child.unwrap()
            continue
        _clean_attributes(child, allow_images=allow_images)
        _walk(child, depth + 1, allow_images=allow_images)


def sanitize_email_html(html: str | None, *, allow_images: bool = False) -> str:
    """Return allowlist-sanitised HTML safe to store and to render.

    ``allow_images`` is the reader's explicit "Load images" action. It defaults
    to False because a remote image in an email is a tracking pixel by default.
    """
    if not html:
        return ""
    source = html[:MAX_HTML_LENGTH]
    soup = BeautifulSoup(source, "html.parser")
    for bad in soup.find_all(list(DROP_WITH_CONTENT)):
        bad.decompose()
    # Conditional comments are a classic way to smuggle markup past a parser
    # that differs from the browser's, so they go before anything is walked.
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    root = soup.body or soup
    _walk(root, 0, allow_images=allow_images)
    return str(root)


# Quote detection is deliberately conservative: we only collapse a trailing
# block when it carries a marker that mail clients themselves emit, because
# guessing wrong would hide content the reader asked to see.
_QUOTE_MARKERS = (
    re.compile(r"^\s*(-{2,}\s*original message\s*-{2,})", re.IGNORECASE),
    re.compile(r"^\s*(on\s+.+\s+wrote:)\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*from:\s.+", re.IGNORECASE),
)
_QUOTE_CLASSES = frozenset({"gmail_quote", "gmail_extra", "moz-cite-prefix", "yahoo_quoted"})


def _blockquote_is_quote(tag: Tag) -> bool:
    classes = {str(c).lower() for c in (tag.get("class") or [])}
    if classes & _QUOTE_CLASSES:
        return True
    if str(tag.get("type", "")).lower() == "cite":
        return True
    if str(tag.get("id", "")).lower().startswith(("divrplyfwdmsg", "appendonsend")):
        return True
    text = tag.get_text(" ", strip=True)[:200]
    return any(marker.match(text) for marker in _QUOTE_MARKERS)


def split_quoted(html: str) -> tuple[str, str]:
    """Split sanitised HTML into (readable body, quoted reply)."""
    if not html:
        return "", ""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.body or soup
    quoted: list[str] = []

    for tag in list(root.find_all(["blockquote", "div"])):
        if not isinstance(tag, Tag) or tag.decomposed:
            continue
        classes = {str(c).lower() for c in (tag.get("class") or [])}
        if classes & _QUOTE_CLASSES or _blockquote_is_quote(tag):
            quoted.append(tag.decode_contents())
            tag.decompose()

    if not quoted:
        # Second pass: a trailing run of plain paragraphs introduced by an
        # "Original Message" style marker, which is how most plain-text replies
        # arrive once they have been converted to HTML.
        children = [c for c in root.children if isinstance(c, Tag) and c.name in {"p", "div", "blockquote"}]
        for index, tag in enumerate(children):
            text = tag.get_text(" ", strip=True)
            if text and any(marker.match(text) for marker in _QUOTE_MARKERS[:2]):
                for tail in children[index:]:
                    if not tail.decomposed:
                        quoted.append(tail.decode_contents())
                        tail.decompose()
                break

    return str(root), "".join(quoted)