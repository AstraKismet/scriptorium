"""Which parser reads a document, and what that parser needs to know.

The registry deferred out of HANDOFF-007 to land with the first non-Markdown
format. A format is ``parse(text, dnt, opts) -> (nodes, segments)`` plus a
``render`` and the small facts around them; it does not fork the pipeline
(``AGENTS.md``, "Conventions"), and everything downstream of ``parse`` —
masking, the memory key, the validators, batching, the workbench — is unchanged
by a format being added.

**Lookup is by extension, with configuration as the only override.** A
``--format`` flag was the alternative and lost: the format has to be the same at
extract and at render or the skeleton is read by a parser that did not write it,
so it is frozen onto the document as ``doc["format"]`` at extract and every later
command reads it from there. A flag would let one invocation disagree with the
next about what a file is, and the disagreement would surface as a corrupted
render rather than as an error.

**An unknown extension is refused, not guessed.** Before this module every path
went through the Markdown parser whatever it was called, which is harmless for a
``.rst`` and silent ruin for anything binary. The message names ``formats.map``,
where one line adds an extension.

**What this registry does not serve yet, deliberately.** A format here is one
decoded string in and one string out. A container — EPUB, whose unit is a zip
entry and whose render has to write the archive back — does not fit that, and
making it fit before anything needs it would be a contract written against a
guess. The two formats that exist are both single-string, and the record below
is what a container format widens rather than what it has to squeeze into.
"""

import os

from . import mdparse, textparse
from .config import TEXT_DEFAULTS
from .skeleton import MARKDOWN_MARKER

__all__ = ["EXTENSIONS", "Format", "UnknownFormat", "by_name", "encodings",
           "for_doc", "for_path", "name_for_path", "options"]

#: The format a state file written before this registry existed was parsed with.
#: There is only one candidate — nothing else could parse a document then — so an
#: absent ``doc["format"]`` is not ambiguous and needs no ``STATE_VERSION`` bump.
#:
#: That argument is sound backwards and *not* forwards, which is deliberate and
#: worth stating rather than leaving for someone to rediscover. A ``.txt`` state
#: file written by this build carries the same ``STATE_VERSION`` as one written
#: before the registry, so an older build does not refuse it — it reads it, finds
#: no ``format``, defaults to Markdown here, and renders the document with the
#: Markdown marker, emitting ``<!-- untranslated s0001 -->`` into a plain-text
#: file. Bumping the version would close that, at the cost of invalidating every
#: state file in existence to protect a case that only occurs when someone
#: downgrades the build under a state directory. Running an older build against
#: newer state is not part of this project's plan, so the version stays and the
#: consequence is recorded instead of paid for.
DEFAULT_FORMAT = "markdown"


class UnknownFormat(ValueError):
    """A document this project has no parser for. The message names the fix."""


class Format:
    """One registered format. A record, so the fields are named at every use."""

    __slots__ = ("name", "parse", "render", "marker", "defaults", "describe")

    def __init__(self, name, parse, render, marker, defaults, describe=None):
        self.name = name
        self.parse = parse
        self.render = render
        #: ``describe(text, opts) -> dict`` of facts the parse resolved that the
        #: document should record — for plain text, which paragraph shape a
        #: heuristic decided the file is in. Merged into the state file and
        #: printed by `lx extract`, so a wrong guess is visible on the line that
        #: made it rather than on page four of the review.
        self.describe = describe or (lambda text, opts: {})
        #: What ``render`` writes for a segment nobody has translated, when the
        #: caller did not ask for the source as a fallback. Per format because
        #: Markdown's HTML comment is invisible in the rendered document and in a
        #: .txt file the same string is visible junk.
        self.marker = marker
        #: Fallbacks for this format's ``formats.<name>`` config block. Every
        #: format declares ``encodings``, because deciding what bytes mean is a
        #: question every format has to answer and only plain text answers with
        #: more than one candidate.
        self.defaults = defaults

    def __repr__(self):                                   # pragma: no cover
        return f"<Format {self.name}>"


_FORMATS = {
    "markdown": Format(
        name="markdown", parse=mdparse.parse, render=mdparse.render,
        marker=MARKDOWN_MARKER,
        # UTF-8 alone, which is exactly what `read_document` did before this
        # existed. Markdown is a format people write in an editor that already
        # decided on UTF-8; plain text is a format people *find*.
        defaults={"encodings": ["utf-8"]},
    ),
    "text": Format(
        name="text", parse=textparse.parse, render=textparse.render,
        marker=textparse.MARKER, defaults=TEXT_DEFAULTS,
        describe=textparse.describe,
    ),
}

#: Extension to format name. Lowercased on both sides at lookup, because a file
#: called ``BOOK.TXT`` is a text file.
EXTENSIONS = {
    ".md": "markdown", ".markdown": "markdown", ".mdown": "markdown",
    ".mkd": "markdown",
    ".txt": "text", ".text": "text",
}


def _normalized(ext):
    ext = str(ext).strip().lower()
    return ext if ext.startswith(".") else "." + ext


def _extension_map(cfg):
    out = dict(EXTENSIONS)
    for ext, name in ((cfg or {}).get("formats", {}).get("map", {}) or {}).items():
        out[_normalized(ext)] = name
    return out


def name_for_path(src, cfg=None):
    """The format name for a path, or raise :class:`UnknownFormat`."""
    table = _extension_map(cfg)
    ext = os.path.splitext(src)[1].lower()
    name = table.get(ext)
    if name is None:
        known = ", ".join(sorted(table))
        raise UnknownFormat(
            f"{src} has no format this project knows how to read"
            f"{f' (extension {ext!r})' if ext else ' — it has no extension'}. "
            f"Known: {known}. If it is one of those under another name, one line in "
            f"lx.config.json says so: "
            f'"formats": {{"map": {{"{ext or ".nfo"}": "text"}}}}.')
    if name not in _FORMATS:
        raise UnknownFormat(
            f"lx.config.json maps {ext!r} to the format {name!r}, which does not exist. "
            f"Available: {', '.join(sorted(_FORMATS))}.")
    return name


def by_name(name):
    """The registered format, or raise :class:`UnknownFormat`."""
    fmt = _FORMATS.get(name)
    if fmt is None:
        raise UnknownFormat(
            f"no parser named {name!r}. Available: {', '.join(sorted(_FORMATS))}. "
            f"A state file naming an unknown format was written by a build that had it — "
            f"upgrade scriptorium, or re-extract the document.")
    return fmt


def for_path(src, cfg=None):
    return by_name(name_for_path(src, cfg))


def for_doc(doc):
    """The format a stored document was parsed with.

    ``doc["format"]`` is absent in every state file written before the registry,
    and those are all Markdown — see :data:`DEFAULT_FORMAT`.
    """
    return by_name(doc.get("format") or DEFAULT_FORMAT)


def options(fmt, cfg):
    """This format's config block, over its own defaults."""
    user = (cfg or {}).get("formats", {}).get(fmt.name, {}) or {}
    merged = dict(fmt.defaults)
    merged.update(user)
    return merged


def encodings(fmt, cfg):
    """Candidate encodings for this format, in the order they are tried."""
    found = options(fmt, cfg).get("encodings")
    return tuple(found) if found else ("utf-8",)
