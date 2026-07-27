# Beyond Markdown

`lx` parses Markdown natively. Other formats reach the same pipeline by converting
them into a segment store, which keeps every downstream guarantee — placeholder
integrity, glossary enforcement, translation memory — unchanged.

## The general shape

Any format reduces to the same two things:

1. a **skeleton** that reproduces the file byte-for-byte given the segment values
2. a list of **translatable strings** with stable ids

For Markdown, `parse()` produces both. For other formats, produce the same two and
reuse everything else.

## JSON / YAML string catalogs

Flatten to dotted key paths and treat each leaf string as a segment whose id is its
key path. Skeleton is the original structure with leaves blanked.

Watch for: ICU message format (`{count, plural, ...}`) — the plural categories
differ per language and the model must be told the target's category set;
placeholders like `{name}` and `%s` are already masked by the `var` pattern.
Keys are not translatable and must never appear in `todo` output.

## gettext PO

`msgid` is the segment source, `msgstr` the target, and the `#:` comments are
useful context to pass through in the `todo` payload. `msgid_plural` needs the
same plural-category handling as ICU. Preserve `#, fuzzy` flags: a fuzzy entry
should extract as pending even though it has a `msgstr`.

## MDX and JSX-flavoured Markdown

Component tags are already masked by the `htmltag` pattern, but **attribute values
are not** — `<Callout title="Read this first">` leaves the title untranslated.
Either add an attribute-extraction pass, or list translatable attributes per
component in config. Do not let the model edit anything between `<` and `>`.

## HTML

Translate text nodes and the `alt`, `title`, `placeholder`, `aria-label`
attributes. Never translate `class`, `id`, `href`, `data-*`. Inline elements
(`<em>`, `<a>`) should be masked rather than segmented, so a sentence stays one
segment and reads naturally.

## Subtitles (SRT/VTT)

Timestamps are skeleton; cue text is a segment. Add a length rule tuned to reading
speed rather than the default ratio check — a subtitle that is technically correct
but 40% too long to read is a defect.

## Source code strings

Extract only from designated string tables, never from arbitrary literals. Anything
that reaches a logger, a database column name, or a switch statement must not be
translated. When in doubt, require an explicit allowlist rather than a denylist.
