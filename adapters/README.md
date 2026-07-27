# Host adapters

The pipeline is a stdlib-only CLI, so every host runs the identical process. Only
the entry point differs.

| Host | Install |
|---|---|
| Claude Skill | drop the whole folder into the skills directory; `SKILL.md` frontmatter handles triggering |
| Claude Code / Codex | copy the folder to `tools/scriptorium/`, then append `AGENTS.md` to the repo-root `AGENTS.md` |
| OpenCode | same folder placement, then `opencode-rule.md` → `.opencode/rules/localization.md` |
| Cursor / Windsurf | same, with `AGENTS.md` content as a project rule |
| No agent at all | `make translate` or the CI job in `examples/ci.yml` |

Keeping the CLI as the product rather than the prompt is what makes this portable:
adapters are ten-line pointers, so a fix to a validator lands everywhere at once and
there is no per-host copy of the logic to drift.
