# Pipeline reference

## State layout

```
lx.config.json            project settings
config/glossary.csv       source,target,forbidden,severity
config/dnt.txt            verbatim do-not-translate terms, one per line
.lx/
  state.db                render skeleton + segments + targets + status, SQLite
  tm.<lang>.jsonl         translation memory, append-only
  reports/<doc>.<lang>.json  last check result
```

`.lx/state.db` is the working state and is safe to delete and rebuild.
`.lx/tm.*.jsonl` is the asset worth keeping in version control: it is what makes
the second, third, and seventh translation of a document nearly free.

## Commands

| Command | Effect |
|---|---|
| `lx init` | write config templates and state dirs |
| `lx extract SRC --lang L` | parse to segments, mask markup, fill from prior state then TM |
| `lx extract SRC --lang L --reset` | discard prior targets for this document |
| `lx todo SRC --lang L` | emit pending segments as JSON |
| `lx todo SRC --lang L --all` | emit every segment, with `fix` notes on failing ones |
| `lx todo SRC --lang L --limit N` | first N pending segments, for batching |
| `lx terms SRC --lang L [--json]` | propose glossary rows from the source; target column left empty |
| `lx terms SRC --lang L --append` | add only candidates the glossary does not have |
| `lx apply SRC --lang L --file F` | ingest translations (`-` reads stdin), auto-normalize |
| `lx check SRC --lang L [--json]` | validate; exit 1 if any error |
| `lx render SRC --lang L -o OUT` | rebuild the target document |
| `lx render SRC --lang L --fallback` | untranslated segments fall back to source |
| `lx commit SRC --lang L` | append approved segments to the TM |
| `lx stats [--lang L]` | coverage across tracked documents |

`apply` accepts three shapes: `{"s0001": "..."}`, `[{"id": "s0001", "text": "..."}]`,
or `{"segments": [...]}`. Unknown ids are reported and ignored rather than failing
the batch.

## Filling the glossary

`lx terms` reads the extracted state, not the raw file, so code spans, URLs and
do-not-translate terms are already masked and cannot be proposed. A candidate is
a maximal run of capitalized tokens joined by exactly one space, seen at least
`terms.min_count` times, **with at least one occurrence that does not open a
sentence** — English capitalizes every sentence's first word, so a token that
only ever appears there is evidence of nothing.

```
$ lx terms novel.md --lang zh-TW
# 47 candidate term(s) from novel.md, seen 2+ time(s); 3 already in config/glossary.csv
# fill in the target column; a row with an empty target enforces nothing
Ashcombe,,,error
Ashcombe Hall,,,error
```

The target column is empty and stays empty: the command does not decide how a
name renders. An unfilled row is inert — it reaches neither the request nor the
check — so `--append` can be run against a live project without changing a
single result until someone writes the renderings in.

Tune the heuristic per project under `"terms"` in `lx.config.json`: `min_count`,
`abbreviations` (a full stop after one of these does not end a sentence, which is
what keeps `Mr. Darcy` discoverable), and `stopwords`.

## Incremental re-translation

A segment is identified by its content and the kind of block it sits in, never by
its position: the memory key is `(content_hash, context, segmentation_version)`
plus a nullable `variant` and the document's register, where `context` is the
block kind for Markdown and for plain text alike. Edit
one paragraph in a 400-segment document and `extract` reports
`reused 399 | pending 1`; move a whole section and nothing goes pending at all.
This is what makes the pipeline usable on a document that goes through many
revisions — nothing is retranslated unless its source actually changed, and
approved wording never silently drifts between versions.

The block kind is in the key because a paragraph translation may wrap across
lines and a blockquote's may not: reusing one as the other puts the second line
outside the quote. It is the block kind and nothing finer — deliberately not the
paragraph's position or its chapter, which would make exact reuse impossible and
make inserting one paragraph invalidate every entry after it.

A memory hit is a proposal, not a result. It is taken only if its placeholders
still match the segment it matched — the same gate model output passes — so
editing `config/dnt.txt` cannot resurrect wording that was masked differently
when it was banked. `extract` reports what it refused:

```
segments 120 | reused 118 | pending 2 | 2 stale memory hit(s) refused
```

Those segments are pending, not damaged, and the memory entry is untouched. Put
the do-not-translate list back and the same entry answers again.

## Batching and routing

Emit 20–40 segments per turn. Smaller batches lose the surrounding context that
makes pronouns and terminology consistent; larger ones raise the cost of a single
malformed JSON response.

A reasonable split when several model tiers are available:

| Work | Tier |
|---|---|
| Bulk draft of reference material, tables, UI strings | fast |
| Prose draft, and the polish pass over it | mid |
| Repair of segments that failed `check` twice, and any legally or contractually load-bearing text | strong |

Repair batches are small by construction — only flagged segments — so spending the
strongest model there costs little.

## Resuming

All progress lives in `.lx/`, so an interrupted job resumes with `lx todo`. There
is no need to replay the conversation, and no need to keep the whole document in
context at any point. For very large documents, loop:

```bash
while python3 -m scriptorium todo doc.md --lang zh-TW --limit 30 | grep -q '"id"'; do
  : translate the emitted batch, write batch.json, then
  python3 -m scriptorium apply doc.md --lang zh-TW --file batch.json
done
```

## Review gate

`check` passing means the mechanical properties hold. It does not mean the
translation reads well. Before `commit`, put the rendered file in front of a human
for the sections that matter — marketing copy, error messages users will see,
anything legal. `commit` is the approval boundary: once a segment is in the TM it
propagates to every future document, so bank only what has been reviewed.

To keep an unreviewed draft out of the TM, simply skip `commit`; `render` works
without it.

## Extending checks

Rules live in `check_segment()` in `src/scriptorium/checks.py`, each a small block appending to
`issues`. Add project rules there and disable built-ins per project with
`"checks_disabled": ["length", "spacing"]` in `lx.config.json`. Anything mechanically
decidable belongs here rather than in a prompt — a rule in code runs on every
segment, forever, at zero marginal cost, and cannot be forgotten under context
pressure.
