# Handoff work packages — the cross-session convention

> **What this is.** The complete convention for Scriptorium's handoff system:
> the rules, the reasoning behind them, the lifecycle, and the failure modes.
>
> **Division of labour with `AGENTS.md`.** The `AGENTS.md` section titled
> *Handoff work packages* is the quick reference that loads into every session,
> and it is authoritative for the red lines and the pickup rule. This file is
> authoritative for the reasoning, the lifecycle, and the pitfalls. Both are
> tracked and must agree; a divergence between them is a maintenance bug, not a
> matter of taste.
>
> **Origin.** Ported from `project-celurion`, whose own section 8 is a porting
> checklist — that document is written to be copied wholesale and adapted. Three
> adaptations were made for this project, each marked *[adapted]* below.

---

## 1. The idea, and why it is shaped this way

**One handoff file is one scheduled work package.** A new session — human or
agent — is pointed at a file, executes it, and deletes it on completion.

The invariants worth keeping if this is ever ported again:

| Design | Reasoning |
|---|---|
| One file per package | No external tool. `ls` is the board. Git does not track it (`handoff/` is in `.gitignore`), so queue churn never pollutes history. What is ignored is the *queue state*, never the *convention* — the convention lives in `AGENTS.md` and in this file, both tracked. |
| Done means deleted | The queue only ever contains work not yet done. Deleting a file simultaneously clears every `blocked-by` that referenced it, so unblocking costs no bookkeeping. |
| A package must be self-contained | The executing session has no memory of the one that wrote the package. Decisions, contracts and red lines must be **distilled into the package**, not merely pointed at. The one exception is section 4's rule for far-future packages. |
| Ids are globally unique and never reused | They are the anchor for `blocked-by` references, and the mapping if this ever migrates to an issue tracker. |
| Folder equals milestone equals order | The numeric prefix sorts lexicographically, which is the priority order. Moving a file between folders is how you reprioritize, and the id does not change. |
| Decisions do not live in handoff files | A handoff is a **carrier**. The formal home for a decision is `docs/` — `docs/decisions.md` for architecture. Long-term memory keeps a one-line pointer and nothing else. |

---

## 2. Directory structure and id ranges

*[adapted]* The source convention uses four folders bound to release milestones.
Scriptorium is single-developer, single-track and has not cut a release, so four
levels would map to nothing real. Section 2 of the source explicitly permits the
simplification:

```
handoff/
  00-inbox/    newly scheduled, not yet ordered (moved out when triaged)
  10-now/      the current milestone            (ids 001–099)
  90-later/    committed but not imminent       (ids 201–299)
```

- **`handoff/` holds work packages and nothing else.** Guides, conventions and
  ledgers do not belong there: a `HANDOFF-*` glob would mistake them for
  packages, and the directory is gitignored, so anything placed there is outside
  version control. This document lives in tracked `docs/conventions/` for exactly
  that reason.
- **An id does not change when a file moves between folders.** The id belongs to
  the package, not to the milestone.
- A new milestone means a new folder and a new id range — after a release, for
  instance, `20-v0.5/` taking 101–199.
- Filename format: `HANDOFF-{id}-{kebab-case-slug}.md`. The slug is for human
  recognition only.

## 3. The pickup rule

Lowest folder in lexicographic order → lowest `priority` (1 is highest; ties
break by id) → **skip anything whose `blocked-by` is not fully cleared**.

The two phrasings that start a session:

- "Execute `handoff/<folder>/HANDOFF-xxx`" — a named package.
- "Take the next executable handoff" — apply the algorithm above.

## 4. The package template

```markdown
---
id: HANDOFF-xxx          # globally unique; becomes the issue title prefix on migration
title: <one sentence>
status: pending          # pending | in-progress (set on claim, with the date)
created: <YYYY-MM-DD>
milestone: <matches the containing folder>
priority: <order within the folder, 1 = highest; ties break by id>
labels: [<see 4.1>]
blocked-by:
  - "<kind>: <detail>"   # empty array means immediately executable; kinds in section 5
blocked-cleared: []      # cleared blockers with their clearing date, for audit
---
## Goal (one paragraph)
## Background and decisions, distilled (enough to start without re-reading sources)
## Scope (IN / OUT)
## Implementation notes (file list, seam ownership, red lines)
## Acceptance criteria (commands and their expected exit codes — see 4.2)
## On completion (always: verify, delete this file, write the next package)
```

**Seam ownership** names which worker owns each file the package touches, and it
is defined in `docs/conventions/delegated-work.md` §1: a file several packages
converge on is edited in one place by one worker, never concurrently. A package
that will be worked by more than one worker states that split here, or it does
not have one.

**Self-containment is graded.** Packages in `00-inbox/` and `10-now/` must be
fully distilled. Packages in `90-later/` may state background as a pointer plus a
summary of the binding constraints — but **completing the distillation is part of
the act of promoting a package** into a nearer folder, not an optional follow-up.

### 4.1 Label pool

*[adapted]* Rewritten in this project's vocabulary:

| Label | Covers |
|---|---|
| `core` | Pipeline engine, masking, normalization, the algorithms themselves |
| `formats` | Parsers and renderers (Markdown, plain text, EPUB, …) |
| `quality` | Validators, terminology consistency, fuzzy matching, quality rules |
| `store` | State layer, translation memory, project model, update propagation |
| `provider` | Model backends, transport, retry, routing |
| `cli` | The command interface and its contract |
| `web` | The review workbench |
| `infra` | Version control, CI/CD, release, packaging |
| `docs` | Documentation, README, conventions |
| `review-backlog` | Decisions taken unilaterally during execution, queued for review |

### 4.2 Acceptance criteria are executable or they do not count

*[adapted]* The *Acceptance criteria* section must state commands and their
expected exit codes. Prose is not accepted. This extends the project's standing
rule that a translation is not done without a green `lx check`, where the exit
code is the evidence — the same standard applies to the work itself.

For example:

```
- `python -m pytest -q` → expect `38 passed` (state the new number if this
  package adds tests)
- `python -m ruff check src tests` → expect exit 0
- `lx check examples/sample.md --lang zh-TW` → expect exit 0
```

## 5. Structured `blocked-by`

One entry per blocker, in `kind: detail` form.

| Kind | Meaning | How it clears |
|---|---|---|
| `user:` | Needs the maintainer's input or decision | Move the entry into `blocked-cleared:` with the date once they have decided |
| `package: HANDOFF-xxx` | Depends on another package | **Clears automatically when that package is deleted**, which is what completing it means. Nothing to edit. |
| `data:` | Missing data — a config table, a real test corpus | Clears when the data lands |
| `design:` | Needs a design ceremony or an architecture decision first | Clears when the decision reaches `docs/` |
| `external:` | External service or infrastructure — a repository, an account | Clears when the external condition is met |

## 6. Lifecycle — five moments

1. **A stage completes → write the next package.** If a pending package already
   exists but this stage made its contents stale, **replace its contents** and
   keep the id. Otherwise create a new file with a new id.
2. **New work appears while nothing is running → add a file.** Undecided ordering
   goes to `00-inbox/`. Never overwrite an existing pending package. Reprioritize
   by moving folders or changing `priority`. Ids are never reused.
3. **A session claims a package → set `status: in-progress (<date>)` before
   starting.** This is what stops two sessions from taking the same package.
4. **Completion → every acceptance criterion passes → delete the file.** Every
   `blocked-by` referencing it is thereby cleared. Then write the next package if
   there is one.
5. **Interrupted → keep the file and add or update a `## Progress` section**
   recording what is finished, how to resume (what the next session should read,
   in what order), and any instruction received during execution that still
   applies to this package.

### Operational notes

- **The progress section is the insurance against a model switch or a quota
  interruption.** On long work, build the skeleton first and write the resumption
  guidance into both the package and the output document — any session, even on a
  different model, can then pick it up from the file alone.
- **Direction received mid-execution lands in two places:** the package's progress
  section and the output document itself. Never leave it only in the conversation.
- **Design questions that surface mid-execution:** decide, implement, and record
  the decision in a `review-backlog` list for the maintainer to overturn in one
  batch afterwards, rather than interrupting per question. The exception is
  anything that is the maintainer's own taste — terminology renderings, style
  calls, naming — which is asked first.

## 7. Relationship to everything else

- **`docs/` is where decisions live.** `docs/decisions.md` is the architecture
  record, and its convention is to write down the alternative that lost. A
  package carries a distilled copy and a pointer; where they disagree, `docs/`
  wins and the package is corrected. **This convention document is itself part of
  `docs/` and is tracked.**
- **Long-term memory keeps one line:** scheduling lives in `handoff/`, the rules
  live in the `AGENTS.md` section and in this file. Never package contents.
- **Git:** `handoff/` is ignored in full and holds only queue state. Work output
  is committed normally. A package's deletion leaves no trace in git, and that is
  the design, not a defect — queue state is not worth versioning, conventions and
  decisions are.
- **Decision boards:** `decision-board*.html` is likewise ignored. A board is a
  transient carrier; the decision's home is `docs/`. Once the report is taken and
  the decisions are recorded, the board is deleted.
- **Delegated work is the other axis.** This convention governs the timeline —
  when a piece of work is picked up, in what order, and when it is deleted.
  `docs/conventions/delegated-work.md` governs the cross-section: what happens
  when one piece of work is split across several workers at the same moment. They
  share no rule, and neither restates the other. A package is the unit of
  scheduling; a brief is the unit of dispatch, and §3 there says what a brief must
  carry beyond what the package already holds.
- **A future issue tracker:** the frontmatter is already an issue-field mapping —
  title, labels, milestone, and `blocked-by` as dependencies. At that point
  "create a package" becomes "open an issue and reference its number locally",
  and "delete a package" becomes "close the issue". The template and the lifecycle
  do not change.

## 8. Failure modes

Of the queue and its lifecycle. For the ways *delegating* a package to several
workers fails — a fixture bent to make a test pass, a contract key invented, a
seam overwritten — see `docs/conventions/delegated-work.md` §6. Two lists, two
subjects; neither supersedes the other.

- **A package that is not self-contained.** If the executing session spends half a
  day reconstructing context, the distillation failed. The test: can a session
  that reads only the package start work?
- **Forgetting to claim.** Two sessions collide. Set `status` before starting.
- **Completing without deleting.** The queue rots and dependants never unblock.
  Once it passes, delete it; anything you are still unsure about goes into the
  next package.
- **A deleted package's OUT list lapses.** Writing "out of scope — that belongs
  with package X" defers the work in the *reader's* mind and nowhere else. The
  package is then deleted and the deferral goes with it, because nothing ever
  wrote it into X. Observed: HANDOFF-003 deferred two `web/server.py` job-layer
  defects to HANDOFF-204, which did not mention them. Before deleting, open every
  package the OUT list names and confirm the item is actually in it — deferring
  is an edit to two files, not a sentence in one.
- **A neighbour that landed first leaves a package stale.** A pending package
  describes code as it was when it was written. When another package changes that
  code, the pending one keeps instructing a future session to fix what is already
  fixed, and its acceptance criteria keep citing numbers that have moved.
  Observed: HANDOFF-003 closed defect (d) of HANDOFF-008 and moved the test count
  the same day. Correcting the neighbour is part of completing the package, and
  the correction says *closed by which package, on what date*, so the record of
  what was measured survives without reading as work still owed.
- **Writing a decision only into the package.** Packages get deleted. The decision
  must reach `docs/` first.
- **Overwriting a pending package.** Only permitted when the just-completed stage
  made its contents stale, and then the id is kept. New requirements always mean a
  new file.
- **Reusing an id.** Never — not even for a cancelled package. Cancelling means
  deleting the file and recording one line in `docs/decisions.md` explaining why.
- **Putting a guide, a convention or a ledger in `handoff/`.** The `HANDOFF-*`
  glob will mistake it for a package, and the directory is outside version
  control. Conventions go in tracked `docs/`; `handoff/` holds work packages only.
- *[adapted]* **Acceptance criteria written as prose.** Criteria without a command
  and an expected exit code are not acceptance criteria.
