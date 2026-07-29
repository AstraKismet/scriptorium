# Why the pipeline is shaped this way

This design replaces a common pattern: a prose rule that instructs the model to
parse the document, substitute placeholder tags, role-play a chain of reviewer
agents, self-verify, and emit a report claiming success. That pattern has five
structural problems, and each one determined a design decision here.

## 1. Structural work was assigned to the model

Masking and unmasking markup is a total function over the input — a program gets it
right every time. A model gets it right *almost* every time, and "almost" compounds:
at 99.5% per node, a 500-node document survives with probability 0.08. The failures
are also the expensive kind, because a dropped code fence is not visibly wrong until
someone runs the code.

Here, `parse()` and `render()` are code, and the model never sees markup at all.
Structure is preserved by construction rather than by instruction, which is why
there is no "did the headings survive" check — the question cannot arise.

## 2. Self-validation is not validation

"Verify that all tags match 100%" asks the model to grade its own homework using the
same faculty that produced the work. Worse, a fixed output template containing
`Tag Restoration Status: 100% Passed` instructs the model to *assert* success
regardless of the facts. A report that cannot fail carries no information.

Here, `lx check` is an external program that returns a nonzero exit code and writes
a machine-readable report. It can fail, which is what makes passing mean something.

## 3. Simulated multi-agent review is one pass wearing three hats

Instructing a model to "internally simulate Agent A, B, and C" produces a single
forward pass with extra framing. There is real value in draft-then-revise — but it
comes from the revision seeing the completed draft, which requires the draft to
exist as an artifact first.

Here, the draft is persisted to `.lx/` and the polish pass reads it back. Same
intuition, actually realized. The auditor role is split: mechanical checking moves
to code, and model review is spent only on segments the checker flagged, where
attention is worth paying for.

## 4. Whole-document, single-shot, non-resumable

No segmentation means no way to translate a document larger than the context window,
no way to resume after a failure, and no way to retranslate only what changed. A
document in its seventh revision gets fully retranslated for the seventh time, and
previously approved wording drifts on every pass.

Here, state is segments on disk keyed by content and block context, plus an
append-only translation memory. Editing one paragraph of a 400-segment document
produces exactly one unit of work, and approved wording is stable across revisions
by default.

## 5. Terminology enforcement was aspirational

"Match against project glossary" with no glossary file, no format, and no check is a
statement of intent. Terminology consistency is the most-complained-about property of
technical translation and it is entirely mechanical: if the source term appears, the
agreed target term must appear.

Here the glossary is a CSV with forbidden variants and per-term severity, injected
into each segment's payload so the model sees only the terms relevant to that
sentence, and enforced by a rule that fails the build.

## The general principle

Sort the work by whether a program can decide it.

| Program can decide | Only a model can decide |
|---|---|
| markup protection and restoration | word choice |
| structural fidelity | register and tone |
| terminology presence | ambiguity resolution |
| punctuation width, CJK/Latin spacing | idiom and cultural adaptation |
| numeric fidelity | sentence restructuring |
| locale vocabulary violations | what a heading is actually about |

Everything in the left column should be code, and most of it should be *fixed*
automatically rather than merely reported — `lx apply` normalizes punctuation and
spacing rather than asking the model to get them right, because the cheapest defect
is the one that cannot be introduced.

The right column is where the model earns its cost, and it is exactly the column a
prompt should talk about. A localization prompt that spends most of its length on
placeholder bookkeeping has spent its most valuable resource — the model's attention —
on the part a shell script does better.
