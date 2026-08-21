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

### 2b. A fan-out's lanes are priced before its topology is chosen

Cutting work into lanes and choosing how those lanes are joined are two decisions,
and getting the second one wrong spends the whole saving the first one bought.

**Measured 2026-08-14**, on HANDOFF-204's M0 reconnaissance. Five read-only lanes
went out behind one barrier: four inventories over the source tree, and one
measurement lane that reproduced three recorded defects and probed the path
primitives behind them. The four inventories returned between **2.3 and 5.6
minutes**. The measurement lane returned at **103.5 minutes**. Because the join
waited for all five, four finished reports were unreadable for ninety-six minutes.
Nothing had failed — zero tool errors, zero retries, and the slow lane wrote to its
transcript continuously the whole time.

Two errors, and they are separable:

1. **The lanes were cut on the wrong axis.** The split was "which lane runs code"
   — one — against "which lanes read files" — four. That is not what prices a lane.
   What prices it is **how many independent proof procedures its brief mandates**:
   the slow lane carried six, each needing its own scratch project, its own
   configuration and its own inspection, and tool round-trips are as serial inside
   one worker as token generation is. This is the batching error of 2026-08-13 on a
   different cost axis, so the rule is stated once for both: **cut a lane by the
   unit of output it must produce — one object, one reproduction, one file — never
   by which capability it happens to use.**

2. **Lanes of different cost classes shared a barrier.** Estimate each lane's cost
   *before* choosing the join, and write the estimate down. When one lane's
   estimate is several times its neighbours', it does not share a barrier with
   them: dispatch it separately, or join with a form that releases each lane as it
   finishes. A barrier is only right when a later stage genuinely needs every prior
   result at once, and reading five reports one after another is not that.

**And a finished lane is readable before the run is.** However the lanes are
joined, a completed worker's return value is recorded the moment it lands — so a
coordinating worker that finds itself waiting should read what has finished rather
than wait for what has not. Check that before concluding a slow run is a stuck one.
The mechanism is tool-specific and lives in the per-machine binding.

### 2c. A finding is routed to verification by what would settle it, not by how bad it sounds

A review that fans out to find things and then fans out again to check them pays
twice, and the second fan-out is where the money goes: it is one worker per
*finding* rather than per *lane*, so it scales with how productive the first stage
was. Two rules keep it honest without keeping it expensive, and both are about the
join between the stages rather than about either stage.

**Measured 2026-08-21**, on HANDOFF-028's adversarial review. Six read-only lenses
returned **32 findings**; the refutation stage was one worker per finding at the
design tier. It was interrupted after **10 of 32** verdicts. What the ten showed:

- **Three of them refuted the same finding.** One stray carriage return in a
  tracked document was reported by three separate lenses, and each report bought
  its own design-tier worker. Across the whole set, 32 findings covered roughly
  twenty distinct issues — the two heaviest were reported by four lenses each.
- **Five of the ten verdicts were "reproducible, and not a defect".** That is the
  refutation stage earning its price, and it earned it entirely on *low-severity*
  findings, where the question was whether a true observation was a defect or a
  preference.
- **The two findings that carried real weight were settled by the coordinating
  worker in a single shell call** — a three-point timing loop, a `grep` for call
  sites, and a byte count. That evidence is *stronger* than a delegated verdict,
  not weaker, and it cost less than dispatching one worker.

So:

1. **Deduplicate before the second fan-out, in plain code.** Independent lenses
   over one change agree far more than they disagree, and every duplicate is a
   whole worker. This is the case where a barrier between the stages is genuinely
   right — merging across the full result set is exactly what a barrier is for —
   and it is the only work in the pipeline that must not be delegated to a model,
   because "are these the same finding" keyed on file and claim is arithmetic.

2. **Route each surviving finding by what would settle it.** A claim with a
   command that decides it — a count, a timing, a grep, an exit code — is settled
   by *running the command*, at no tier at all. A claim whose real question is
   "is this a defect or a preference" is judgement, and judgement is what the
   design tier is for. Severity is not the router: a `high` finding is often the
   mechanical one, because what makes it high is usually that it is measurable.

3. **Make the reporting lane declare what would settle its own claim**, as a
   required field beside the claim itself. The lane that found it knows; the
   coordinator guessing afterwards is re-deriving something that was free at the
   source, and a lane asked the question writes better findings for having been
   asked it.

The residue is a real one and belongs in the closing report rather than hidden: a
review whose verification stage is skipped or cut short has findings of **more than
one evidential kind**, and a list that does not say which is which reads as
uniform. Mark each item with how it was established — measured here, refuted
independently, or reported once and unchecked — and give whoever acts on it the
rule that an unchecked low-severity item is verified *before* it is repaired, with
the outcome recorded either way. An item quietly dropped for being a preference is
an item the next reader re-derives from nothing.

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

   **And the reference itself is a measurement.** A differential sweep compares
   your code against another implementation, so if the two do not implement the
   same feature set the sweep is measuring that difference and reporting it as
   your defect. Measured 2026-08-03, HANDOFF-021: the reference was run without
   its table extension while the code under test implements tables, and every
   question about the line *after* a table got the opposite answer — 84 reported
   regressions, all of them phantom, all of them gone the moment the extension
   was enabled. State which configuration of the reference produced a number, the
   way you state the axes.

   **Watch a metric you are not being scored on.** The same sweep's regression
   count was a true 0 while a real regression sat in a diagnostic count nobody
   had asked about, and the suite was green through both. A number that moves in
   the wrong direction is worth a paragraph even when it is not the number the
   package is about; it is the cheapest place a missed axis shows up.
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
| 2026-08-12 | The credential rules of `lx config set` — `_field_api_key_env`, `_field_base_url`, `_field_headers`, `_field_rule`/`_validated` and the hardened `dump_json` (HANDOFF-206) | Security | Coordinating worker, general top tier | The rule *set* was designed at the security tier and frozen before any code; the code that implements it was then written by the coordinating worker rather than sent back. §4 lists "credential handling" and "review of any of these" in the security row and does not name implementation, so this is the row's edge rather than its centre — logged anyway, because the implementation is where the rules either hold or do not. | 2026-08-12 |
| 2026-08-12 | ↑ cleared by re-derivation | Security | Security | A security-tier pass attacked the written code — not the design — and found what the design could not: `providers.*.api_key_env` was absent from `_WHOLE_BLOCK`, so `lx config set providers.newbackend.api_key_env.x sk_live_…` exited 0 and wrote a raw credential into the committed file, with `_field_api_key_env` never consulted. The four providers `lx init` scaffolds were incidentally safe and every backend a person adds was not. It also found `base_url` echoing a rejected value and accepting a `?key=` query. All reproduced live, fixed, and pinned by a test each. **The lesson for the next dispatch:** a frozen rule set does not make its implementation routine — the design pass had specified "a rule is applied where a field lands" and the first implementation of that sentence satisfied it for one spelling out of two. | — |
| 2026-08-13 | The trust-boundary sections of `docs/contracts/workbench-http.md` — *Request admission*, *Path and language confinement*, and the security half of *Deliberately not in the contract* (HANDOFF-207) | Security | Coordinating worker, general top tier | The package's deliverable is one document and §4 puts "review of a trust boundary" in the security row, so the sections stating one are security work even though nothing was designed or implemented. Written at the general top tier and sent for re-derivation rather than split across two tiers mid-document. | 2026-08-13 |
| 2026-08-13 | ↑ cleared by re-derivation | Security | Security | A security-tier pass derived the trust boundary from the code **before** reading the document, then compared, and returned **NOT CLEARED** on two counts. (1) The *Reserved* entry for `--allow-origin` said the flag "changes no request or response shape, so it would not bump the contract version" — classifying a trust-boundary widening as the versioning rule's additive "wider accepted value set", and thereby pre-authorizing the `Access-Control-Allow-*` / `do_OPTIONS` step that `docs/decisions.md`, 2026-07-29 says "silently reopens all of this". The document written to stop the next reader reopening the hole had reopened it one layer up. (2) `Provider.describe()` and the transport-failure message interpolate the **raw** `base_url`, so a userinfo-bearing URL is masked by `lx providers` and printed in full by `lx translate` and by `POST /api/job`'s `log` and `error` — an invariant 6 defect the enumerated three-surface list in `AGENTS.md` had hidden. Both fixed, both pinned by a test, and the mutation pass kills both halves. **The lesson for the next dispatch**, and it is the third consecutive one of this shape: what survives a top-tier pass is not a wrong rule but a correctly stated rule whose *enumeration* was read as its definition. | — |
