# Delegated work

Rules for the case where work on this repository is split across more than one
worker — a second person, a second session, or several assistant processes
running concurrently. `docs/conventions/handoff-workflow.md` governs the other
axis: which work is picked up when, and by whom, over time. This file governs one
moment, across workers. They exist because the failure modes of delegation are not
the failure modes of working alone: what breaks is not the code a worker writes
but the context it was never given.

This file names mechanisms and artifacts, never tools. The tool-specific binding
— which worker type, which capability tier, which flag — lives in a per-machine
file at `.claude/orchestration.md` when one is present, because a roster of tool
names goes stale silently (`docs/decisions.md`, 2026-07-28, process-tool names).
Naming the path is not naming the tool: `.gitignore` governs `graphify-out/` the
same way. Where the two files disagree, this one is right, and no rule may live
only in the binding.

## 1. What may not be delegated

**Shared seams are edited by one worker, in one place.** `mask.py`, `checks.py`,
`mdparse.py` and `store.py` are touched by nearly every package in the queue. Two
workers editing one seam concurrently do not produce a merge conflict — they
produce one worker's version silently overwriting the other's, with both sets of
tests green, because each tested only its own half. The coordinating worker makes
seam edits itself, surgically, even when the surrounding work was delegated.

**Decisions that touch an invariant.** The invariants in `AGENTS.md` are
decisions with recorded losing alternatives. Re-deciding one costs an entry in
`docs/decisions.md`, and a worker that never read the losing alternatives cannot
know it is re-deciding.

**The judgement half of the pipeline.** Whether a lexicon row is mechanically
decidable (invariant 4), whether a defect is deterministically fixable (invariant
5), whether a placeholder pairing rule is complete (invariant 2b) — these are the
questions this project exists to answer carefully. Delegate the enumeration, keep
the judgement.

## 2. What delegates well here

Work that is wide, independent, and mechanically checkable:

- Enumerating round-trip properties and writing one corpus fixture per property.
- Auditing a rule set row by row — the lexicon audit is the standing example.
- Locating things: which call sites use a function, where a pattern appears.
- Generating independent options for a decision, to be synthesized by one worker
  afterwards.

Note what these have in common: the coordinating worker re-reads the output and
is capable of noticing when it is wrong. Delegation is safe in proportion to that
capability, not in proportion to how well the task was described.

### 2a. A worker that writes files is isolated, and the tree is clean first

Every item above is read-only, which is why delegation here has been safe. The
moment a delegated worker **writes** — mutation testing, a mechanical migration,
a fan-out of edits — two things become mandatory:

1. **It runs in its own git worktree**, not in the shared checkout.
2. **The shared checkout is committed-clean before dispatch.** Uncommitted work
   is what gets destroyed, and it leaves no trace in history to recover from.

Both, not either. **Measured 2026-08-02**, and the mechanism is worth stating
because it is invisible until it happens: reverting an injected mutation is
`git checkout -- <file>`, whose semantics are "discard this file's unstaged
changes". It cannot distinguish the agent's mutation from a human edit in
progress, so it took both. A `git add -A` that raced an injection then committed
a mutation into the branch. Three conditions had to coincide — the worker writes
*and reverts*, it shares the working directory, and edits were uncommitted — and
ordinary read-only delegation never meets the first, which is why this had not
happened before and why nobody expects it.

Forbid `git checkout --`, `git restore`, `git reset` and `git stash` in the brief
even so: a worktree bounds the damage, it does not prevent a worker from
destroying its own assigned work and reporting success. And clean the worktrees
up afterwards — `git worktree remove`, then `git branch -D` — because they
persist past the session that made them, and a `git worktree list` full of dead
entries is where a live one hides. `.gitignore` carries `.claude/worktrees/` for
the same reason.

## 3. The brief carries the context, or the worker invents one

A delegated worker does not investigate before acting and does not know anything
that was not written down for it. Everything not distilled into its brief is
therefore something it will silently substitute a plausible guess for. This is
the root cause of contract key names drifting, of shared code being reimplemented
instead of imported, and of a simplification nobody asked for.

Before dispatching, the coordinating worker confirms the brief carries:

- The package or milestone entry, and the acceptance criteria verbatim.
- **The distilled decisions and constraints that apply** — the relevant
  invariants, the relevant `docs/decisions.md` entries, and any contract this
  work must not break.
- The exact contract: key names, field shapes, function signatures.
- Which files this worker owns, and explicitly which it must not touch.

Parallel workers get disjoint file sets. If two briefs name the same file, they
are one brief.

## 4. Capability tiers

State the tier by what the work *is*, so the rule survives being renamed:

| Work | Tier |
|---|---|
| Locating and retrieval — where is X, which callers, does this pattern occur | Lowest. Retrieval does not improve with capability, it only costs more. |
| Fact mapping and inventory — what the code currently does, what exists | Middle. |
| Implementation against a fully specified contract | Middle, or the coordinating worker directly. |
| **Design whose output will be frozen** — a schema shape, a public API, a cross-module contract, or the option set that goes into a decision | **Highest.** |
| **Security work** — threat modelling, trust boundaries, credential handling, attack surface, review of any of these | **Highest available, and this is the only work that gets the security-specialized tier.** |
| Synthesis, cons-mitigation, and putting a decision to the user | Not delegated. The coordinating worker does it. |

Two clarifications that are load-bearing:

- Retrieval has a **ceiling**, not just a floor: spending the top tier on a
  search is waste, and waste of the top tier is what makes people ration it where
  it matters.
- "Design" is not a synonym for "hard". The test is whether the output gets
  frozen — a schema, a contract, an option set someone will choose from. Mapping
  what the code does today is inventory, not design, however intricate.

This repository has real work in the security row: the workbench path and origin
hardening, and the credential-handling rules around a writable configuration.
It is not a hypothetical lane here.

## 5. Downgrades are recorded, not absorbed

When the required tier is unavailable — quota, availability, or the session
already running elsewhere — the coordinating worker does the work itself at
whatever tier it has, and **both** of the following happen, neither alone:

1. The output is marked as produced below the required tier, at the point of
   output, where the next reader will see it.
2. A line is added to the ledger in §7.

Only two situations are downgrades: design that did not get the top tier, and
security work that did not get the security tier. Everything else is just work.

**Re-review means re-deriving, not re-reading and agreeing.** A downgraded
artifact is cleared by working the problem again at the required tier and
comparing conclusions. Reading it over and finding it reasonable is how a wrong
answer gets ratified twice. Recording which model produced what is consistent
with `docs/decisions.md` C3, which keeps `Co-Authored-By` trailers for exactly
this reason.

**The gate for anything feeding a decision is before the decision, never after.**
Whoever decides is choosing from the options in front of them; an option set with
a gap in it looks complete from the inside, and no later review recovers the
option that was never written.

## 6. Failure modes measured on projects of this shape

Of delegation. For the ways the *queue* fails — a package that is not
self-contained, a completed package that was never deleted, a reused id — see
`docs/conventions/handoff-workflow.md` §8. Two lists, two subjects.

Watch for these specifically when reviewing delegated output. Each has been
observed; none is theoretical.

1. **The test is bent to fit the code.** Here this has a hard form: `tests/corpus/`
   fixtures are **never** edited to make a test pass (`AGENTS.md`, Commands). A
   failing fixture means the parser is wrong or the fixture is not valid input.
2. **Contract key names drift** — the worker invents a field name because the
   brief did not carry the real one.
3. **A shared seam is reimplemented** rather than imported, and the reimplementation
   overwrites the original.
4. **The task drifts and part of the deliverable is quietly dropped.** Check the
   output against the acceptance criteria as a list, not as an impression.
5. **An invariant is broken in a way the tests do not see** — a slot value written
   without host escaping (2b), a raw node stored as text rather than bytes (2a), a
   judgement rule added to `checks.py` (4), a dependency added (1), a credential
   reaching a log line (6).
6. **Tracked documentation written in a language other than English**, or a
   process-tool name written into a tracked file.
7. **A recorded measurement is accepted instead of re-derived.** Observed
   2026-08-02: a comment justified a refactor with "1.59M combinations, all
   differences unreachable". The twelve line shapes it swept contained no blank
   or whitespace-only *target*; adding six moved the count from 3 of 864 to 129
   of 1944. Three reviews had read that comment and agreed with it. A large
   combination count across a shape set missing one dimension reads like proof
   and is not — so when reviewing a measurement, check which *axes* it varied
   and what each looks like at its degenerate end (empty, whitespace, one
   element, absent), rather than checking the total. This is §5's rule applied
   to evidence: re-review means re-deriving, not reading and agreeing.

   **The same rule pointed at a measurement you produced yourself.** A sweep is
   blind to the axis it does not vary, and scaling inside the remaining
   dimensions never reaches a dimension that is absent — so a big number is
   evidence about the axes you chose, never about the ones you did not. Before
   trusting your own zero: write down the axes the sweep varied, name what it
   held constant, and hand the **claim** — not the sweep — to an adversarial pass
   whose job is to find the missing axis rather than to re-run the same one
   wider. Record the axis list beside the number, because a number without it
   cannot be reviewed under the paragraph above.

   Measured twice, on consecutive packages. HANDOFF-018: 37224 generated
   documents, 0 counterexamples reported, and adversarial review then found four
   regressions — all on the one axis the sweep never varied, the shape of the
   chunk's own lines. HANDOFF-019, whose author had that entry in front of them:
   441 documents across five named axes said the trailing half of a strip was
   harmless everywhere, and the axis held constant was *whether a line the
   skeleton owns follows the segment*. Twenty hand-built cases on that axis alone
   found six structural shapes. The second one is the more useful of the two,
   because knowing the failure mode did not prevent it — only enumerating the
   axes did.
8. **A writing worker destroys work it was not given.** See §2a; the countermeasure
   is isolation before dispatch rather than review afterwards, because the damage
   is to uncommitted state that no review can see.

The countermeasure is the same for the first seven: an adversarial review pass
that is always run, plus the coordinating worker checking deliverables against
the criteria and re-running `python -m pytest -q` and
`python -m ruff check src tests` itself rather than trusting a report that they
passed. The eighth is the exception that proves the shape of the rule — review
runs after the work, and the damage there is already done before any review
begins, so it is answered by §2a's isolation instead.

## 7. Downgrade ledger

Append one line per downgrade. Never edit or delete a line; a cleared downgrade
gets a second line recording the re-derivation.

| Date | Artifact | Required tier | Tier used | Reason | Cleared |
|---|---|---|---|---|---|
| 2026-07-29 | `language_tag` — the `lang` whitelist and its enforcement points (HANDOFF-008) | Security | Coordinating worker, general top tier | The security-tier design pass did not find the `lang` vector at all. The coordinating worker measured it and specified the rule while freezing the contract, rather than sending a second design round. | 2026-07-29 |
| 2026-07-29 | ↑ cleared by re-derivation | Security | Security | Three independent security-tier reviews attacked `language_tag` directly — every separator, `..`, empty, over-length, NUL, and every non-string JSON value — and found a real defect the original rule missed: `dict.get` cannot distinguish an absent key from a JSON `null`, so `{"lang": null}` skipped the whitelist entirely. Fixed at the security tier and pinned by a test. Re-derived, not re-read. | — |
