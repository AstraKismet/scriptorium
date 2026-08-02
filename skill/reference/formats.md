# Formats

`lx` parses **Markdown** and **plain text** natively. Anything else reaches the
same pipeline by being converted into a segment store, which keeps every
downstream guarantee — placeholder integrity, glossary enforcement, translation
memory — unchanged.

## The general shape

Any format reduces to the same two things:

1. a **skeleton** that reproduces the file byte-for-byte given the segment values
2. a list of **translatable strings** with stable ids

`parse(text, dnt, opts) -> (nodes, segments)` produces both, and `render` is
shared: it walks the skeleton and knows nothing about syntax.

Which parser reads a file is decided by its extension — `.md`, `.markdown`,
`.mdown`, `.mkd`; `.txt`, `.text` — and frozen onto the document at `lx extract`,
because a skeleton is only readable by the parser that wrote it. An unknown
extension is refused rather than guessed at. One line in `lx.config.json` maps
another one:

```json
{ "formats": { "map": { ".nfo": "text" } } }
```

## Plain text

The format novels arrive in, and the one place this pipeline runs on heuristics
rather than on rules. All three are configuration, under `formats.text`, because
a guess about someone else's file is judgement and judgement stays out of
`checks.py`.

**Encoding.** Candidates are tried in order with strict decoding and the first
that works wins; a byte-order mark overrides the list entirely and is kept, not
stripped. The default order is `["utf-8", "shift_jis", "cp950", "gbk",
"cp1252"]` — `cp950` rather than `big5` because Python's `big5` codec rejects
ordinary Windows characters such as 裏, and a file it rejects falls through to
`cp1252` and becomes Latin-1 gibberish. A file whose bytes are invalid in every
candidate is **refused**, never repaired with replacement characters. `lx
extract` prints the codec it chose; if it is wrong, reorder the list.

**Paragraphs.** `paragraph_mode` is `auto`, `blank-line`, `line` or `indent`.
Auto chooses between the first two by asking whether a blank line ever separates
two runs of text. It never guesses `indent` — hard-wrapped with an indented
first line and no blank lines — because the test that would detect it also fires
on a one-paragraph-per-line file containing a single indented line, and guessing
wrong there joins the whole book. `lx extract` prints what it chose.

**Chapters.** `chapter_patterns` decides which one-line block is a `heading`
rather than a `para`. Both directions are cheap: a missed title is still
translated, and a false positive costs the kind and its memory context. Editing
the patterns later is *not* cheap — the kind is the memory context, so a block
that changes kind orphans its banked wording while its text stays identical.

## Adding a format

Implement `parse`, register it in `formats.py` with its untranslated marker, its
encoding candidates and its config defaults, and add a `checks.py` host profile
saying which line shapes open a block and which kinds may not gain a line. Do not
fork the pipeline. The registry serves formats whose document is one decoded
string; a container — EPUB, whose unit is a zip entry — widens it.

The rest of this file is guidance for formats that are *not* built in.

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
