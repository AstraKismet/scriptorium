# OpenCode Rule: Scriptorium Localization

Place this at `.opencode/rules/localization.md`, or point OpenCode at the repo-root
`AGENTS.md`, which OpenCode also reads.

## Scope

Applies to every translation, internationalization, or multi-language adaptation
request in this workspace.

## Rule

Translation is a pipeline, not a prompt. `lx` owns document
parsing, markup protection, reassembly, terminology enforcement, and punctuation
normalization. Your job is the prose inside each segment.

1. `lx extract <src> --lang <lang>`
2. `lx todo <src> --lang <lang>` and translate the returned segments
3. `lx apply <src> --lang <lang> --file draft.json`
4. `lx check <src> --lang <lang>` — repair only flagged segments, repeat until exit 0
5. `lx render <src> --lang <lang> -o i18n/<lang>/<path>`
6. `lx commit <src> --lang <lang>` after a human has reviewed the rendered file

Read `tools/scriptorium/skill/SKILL.md` for the segment payload format and
`tools/scriptorium/skill/reference/` for language- and format-specific guidance.

## Constraints

- `⟦n⟧` placeholders are opaque. Copy verbatim; reposition as grammar requires;
  never create or delete one.
- Do not hand-edit files under `.lx/` or hand-write a localization report.
- Do not claim success without a green `check`.
- Terminology comes from `config/glossary.csv`. If a needed term is missing, add a
  row rather than deciding case by case. `lx terms <src> --lang <lang>` proposes
  the rows from the source; it leaves the target column empty, and filling it in
  is the human's call, not yours.
