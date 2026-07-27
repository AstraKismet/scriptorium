# Decision log

Short entries, newest first. Record the alternative that lost, not just the
choice that won — the reasoning is what future changes need.

## 2026-07 · Placeholders use ⟦n⟧ rather than [[TYPE_NNN]]

The original design used typed, per-type-numbered tags like `[[PROTECTED_CODE_001]]`.
Three problems: the words inside are translatable-looking and models occasionally
localize them; per-type counters make the id space ambiguous when reordering; and
square brackets collide with Markdown link syntax.

`⟦n⟧` is a single sequential integer in brackets that essentially never occur in
prose, so validation is exact-multiset comparison and mangling is repairable.
The type is not encoded because nothing downstream needs it — the slot map holds
the original content.

## 2026-07 · Normalization happens on ingest, not as a check

Punctuation width and CJK/Latin spacing were initially reported as warnings. Every
warning costs reviewer attention, and both are fully decidable, so they are now
corrected in `apply` and only reported when something survives correction (which
means the segment is unusual and worth a look).

Rejected alternative: instructing the model to get them right. That spends the
model's attention on work a regex does perfectly.

## 2026-07 · Provider requests stay minimal by default

`response_format: json_object` improves reliability on hosted APIs but is rejected
outright by several self-hosted runtimes rather than ignored. Since local
deployment is a requirement, the default request omits it and JSON is obtained by
prompt plus a tolerant parser. Projects on hosted APIs can opt in per provider
with `"json_mode": true`.

## 2026-07 · Repair loop stops when a round changes nothing

Three fixed rounds burned tokens on models that returned identical output each
time. The loop now compares the failing set's targets between rounds and stops
when they match, which converts a silent waste into an actionable message.

## 2026-07 · Translation memory is committed to version control

`.lx/docs/` and `.lx/reports/` are regenerable and gitignored. `.lx/tm.*.jsonl` is
not: it is the accumulated approved wording, and losing it means retranslating
work that a human already reviewed.
