"""Whether a stored translation belongs to the source it is filed under.

A model was measured answering a batch **by position rather than by id** — every
answer filed one place along, a contiguous cascade. `translate.misattributed`
throws such a reply away now; this module is about the wordings that were banked
before it did.

The instrument is a cross-lingual embedding. For every stored pair, ask whether
the target sits closer to its own source than to any other source in the same
store; a target that is nearer somebody else's source by more than a margin is
reported, together with the record it appears to belong to.

**It reports and it never repairs**, and nothing else in the pipeline consults
it. That is what keeps `lx check`'s exit code — the thing invariant 10 says is
the evidence — from coming to depend on a machine being up, and it is why the
threshold below may exist at all: invariant 4 keeps judgement out of
`checks.py`, and `translate.misattributed` records refusing a similarity test for
exactly that reason. A rule that only ever *says* something is on the other side
of that line from a rule that decides a build.

Three things it structurally cannot see, all of them printed with every report
rather than left to be discovered:

* **A target that translates none of the sources in the store.** The question is
  comparative; a wording that is simply wrong, and wrong about nothing in
  particular, is nearest its own source and passes.
* **A target whose true source is byte-identical to the one it is filed under.**
  Identical text embeds identically, so that rival scores exactly what the
  diagonal scores and can never win. Note what this does *not* say: a record
  whose source merely appears twice flags freely, on some other rival — only the
  rival that would expose it is silenced. Written the other way round, a filter
  that skipped duplicated sources would have deleted 16 of the 17 findings in
  the measured file, and the brief this module was designed from asserted
  exactly that. Two records can share a source and both be examined only when
  something else in the memory key separates them — `context`, `variant` or the
  register — because otherwise the later one supersedes the earlier and
  `memory_pairs` sees one. In the measured file nothing does: 89 of its 208
  *lines* carry a duplicated source and **none of its 157 effective records
  does**, so that file's own duplicates are the superseded half. A document's
  segments are the other case, where the same sentence twice is two positions
  and both are compared.
* **Its own false-positive rate at a size it has not been measured at.** The
  score is a maximum over every other record, so what a clean record scores can
  only grow with the store: subsampling the measured file, the *median* worst
  delta over records known clean runs 0.0000 at 25 records, 0.0217 at 100 and
  0.0700 at 208, against a lowest true positive of +0.1166 — while the *maximum*
  over draws is 0.0700 at every size, because it is one particular pair rather
  than a frontier. No clean record crosses 0.10 at any size measured, and the
  margin below was chosen on 208 records and is not validated for thousands.
  `own` and `delta` ride on every finding so a reader can see how near the edge
  each one sat. Recall moves the other way for a reason no threshold reaches:
  the record a target belongs to has to be *in the audited set*, and over the
  same draws it is found 42.7% of the time at 25 records and 93.1% at 150.

See `docs/decisions.md`, 2026-09-06, for the measurements and for the two rules
that were built, scored and removed.
"""

import string
import unicodedata
from array import array
from operator import mul

from .mask import PH_RE

# `providers.errors` is the narrowest import that names this class, and today it
# saves nothing: Python executes `providers/__init__.py` before it can bind a
# submodule, and that file imports `base`, which imports `urllib.request` and
# with it `ssl`. `errors.py`'s own docstring and `cli.py`'s import comment both
# claim otherwise and both are wrong — measured 2026-09-06, `import
# scriptorium.providers.errors` alone loads `ssl` and fifteen `email` submodules.
# No regression: `cli.py` already paid it. It stays spelled this way because it
# is the import that would become cheap the day that premise is made true, which
# is HANDOFF-054.
from .providers.errors import ProviderError
from .store import tm_effective, tm_path

#: How much closer to another source a target must sit before it is reported.
#:
#: Measured 2026-09-06 against the maintainer's tracked `.lx/tm.zh-TW.jsonl`,
#: 208 records, every flagged pair read by hand:
#:
#: ===========  =======  ================
#: margin       flagged  false positives
#: ===========  =======  ================
#: 0.02         20       3
#: 0.05         18       1
#: 0.10         17       0
#: 0.15         16       0 (one true positive lost)
#: ===========  =======  ================
#:
#: 0.10 is the corner: the largest margin that keeps all seventeen and the
#: smallest that admits none of the near-duplicate false positives. It is also
#: about 770 times the instrument's own reproducibility — the same string
#: embedded at two positions of one batch was measured differing by 1.3e-4 of a
#: cosine — so a finding here cannot be manufactured by which batch a record
#: landed in.
#:
#: **A module constant with a flag over it, and deliberately not a config key.**
#: A per-project threshold lets two runs of one command over one file disagree
#: with nothing in either report saying why; `--margin` is per invocation and is
#: echoed into the report, so a finding always travels with the number that
#: produced it. `docs/decisions.md` of 2026-09-02 records what a per-project
#: band cost when `checks.length_ratio` had one.
MARGIN = 0.10

#: How many texts go in one request. Fixed, and the batching is a pure function
#: of the store, because the instrument is not batch-invariant: the same string
#: at two positions of one batch came back differing by 1.3e-4 (measured), so a
#: batch size that varied with anything would make two runs disagree in the third
#: decimal place for no reason a reader could see.
BATCH = 16

#: The largest input this will offer a backend, as an estimated token count.
#:
#: **A property of the server, not of the model.** `bge-m3` reads 8192 tokens;
#: the development `llama-server` refuses any single input above its physical
#: batch size with `HTTP 500 input (530 tokens) is too large to process ...
#: current batch size: 512` — and 500 is in `providers.base._RETRYABLE`, so
#: without this the refusal is retried before it is believed. The number is that
#: measured limit, and a backend with a different one is handled by
#: :func:`embed_texts`' per-input pass either way.
TOKEN_CEILING = 512

#: What a character of each kind costs, measured 2026-09-06 against `bge-m3`
#: over twenty-six samples: six scripts, the ASCII shapes that are cheap and
#: expensive for opposite reasons, and the longest real records in the
#: maintainer's own memory file.
#:
#: ============================  ===============  =========
#: kind                          measured         used here
#: ============================  ===============  =========
#: one ``⟦n⟧`` placeholder       2.25, flat       3.0
#: whitespace                    ~0.007           0.10
#: ASCII letter or digit         0.27 – 0.37      0.42
#: ASCII punctuation             0.48 – 0.97      1.00
#: CJK, kana, Hangul             0.61 – 0.91      0.95
#: other non-ASCII               0.30 – 0.35      0.45
#: ============================  ===============  =========
#:
#: A placeholder is counted rather than averaged in, and that is the correction
#: that mattered: `⟦` and `⟧` are punctuation outside ASCII, the first version of
#: this counted them at the cheapest rate available, and a 592-character
#: Traditional Chinese target carrying four of them estimated 301 tokens against
#: a real 547 — the backend refused it. Placeholders are this project's own
#: construct, `mask.PH_RE` finds them, and the cost is flat: `⟦1⟧`, `⟦12⟧` and
#: `⟦123⟧` were each measured at 2.25.
_PH_TOKENS = 3.0
_PER_SPACE = 0.10
_PER_ALNUM = 0.42
_PER_PUNCT = 1.00
_PER_DENSE = 0.95
_PER_WIDE = 0.45

_SPACE_CHARS = frozenset(" \t\n\r\f\v")
_ALNUM_CHARS = frozenset(string.ascii_letters + string.digits)

#: The scripts that cost about a token a character. Category ``Lo`` — a letter
#: with no case — is CJK, kana, Hangul, Thai and the abugidas, and the two
#: ranges beside it are CJK punctuation and the fullwidth forms, which are
#: `Po`/`Ps`/`Pe` and just as expensive. Cyrillic, Greek and accented Latin are
#: cased letters and cost a third of that, which is why they are not in here:
#: folding them in over-estimated a Russian paragraph by 2.35 and would have
#: skipped records that fit comfortably.
_CJK_PUNCT = range(0x3000, 0x3040)
_FULLWIDTH = range(0xFF00, 0xFFF0)


def estimated_tokens(text):
    """About how many tokens ``text`` will cost, from the table above.

    **It errs in both directions and neither one loses a record**, which is what
    lets it be an estimate at all. Over twenty-six measured samples it runs 0.91
    to 1.46 of the real count. Where it reads high, the record is skipped and
    named in the report. Where it reads low, the input is offered, the backend
    refuses it, and :func:`embed_texts` isolates and names it with the server's
    own words. The first version of this claimed the opposite asymmetry —
    "over-skips English rather than under-skipping CJK" — and then under-counted
    the one shape this project's targets actually have.

    "Dense" is decided by Unicode category and range, **not by `mask.CJK_RE`**.
    That constant is pinned to where `mdparse` puts segment boundaries, and
    `mask.py` records the exclusion of kana and Hangul as a limitation of the
    range it belongs to; borrowing it here would inherit a fact about block
    structure to answer a question about token density.
    """
    ph = len(PH_RE.findall(text))
    rest = PH_RE.sub("", text)
    space = alnum = punct = dense = wide = 0
    for ch in rest:
        code = ord(ch)
        if code > 127:
            if (unicodedata.category(ch) == "Lo" or code in _CJK_PUNCT
                    or code in _FULLWIDTH):
                dense += 1
            else:
                wide += 1
        elif ch in _SPACE_CHARS:
            space += 1
        elif ch in _ALNUM_CHARS:
            alnum += 1
        else:
            punct += 1
    return (_PH_TOKENS * ph + _PER_SPACE * space + _PER_ALNUM * alnum
            + _PER_PUNCT * punct + _PER_DENSE * dense + _PER_WIDE * wide)


def oversize(text):
    """Whether this input is long enough that offering it is not worth a request."""
    return estimated_tokens(text) > TOKEN_CEILING


def unit(vec):
    """``vec`` scaled to length one, or ``None`` if it has no length.

    A zero vector is the one reply defect this module survives per record rather
    than refusing whole: one degenerate input does not condemn a run, and the
    record it belongs to is reported as not compared. Everything else the
    backend can get wrong is `providers.base.Provider._vectors`' business and is
    refused there.

    Vectors were measured arriving normalized to within about 5e-8, and this is
    done anyway — a measurement of one model is not a property of the endpoint.
    """
    norm = sum(map(mul, vec, vec)) ** 0.5
    if norm < 1e-12:
        return None
    return array("f", (x / norm for x in vec))


def cosine(a, b):
    """The dot product of two unit vectors.

    `sum(map(mul, ...))` rather than a generator over `zip`, measured 2.1 times
    faster on this shape, which is the whole inner loop of an O(n²) comparison.
    """
    return sum(map(mul, a, b))


def memory_pairs(lang):
    """``(pairs, superseded)`` for the translation memory of one language.

    **The effective records, not the lines.** `store.tm_effective` returns what
    `store.load_tm` returns — the last record under each key — because that is
    what every document in the project actually reads, and because a repair here
    is an *append*: `lx commit` writes a corrected record, the later one wins,
    and the displaced line stays in the file for ever. An audit over the lines
    would name records no command reads, and would go on naming them after the
    reviewer had repaired everything, so the reviewer could never see that their
    repair had worked. Measured on the tracked file: 208 lines, 157 wordings, 17
    lines carrying a misattributed target and **2** of them the record anything
    reads. `superseded` is reported so that the difference between the two
    numbers is on the page rather than hidden by it.
    """
    rows, superseded = tm_effective(lang)
    pairs = []
    for lineno, rec in rows:
        pairs.append({"ref": {"line": lineno},
                      "source": rec.get("source") or "",
                      "target": rec.get("target") or "",
                      "waived": bool(rec.get("waived"))})
    return pairs, superseded


def document_pairs(doc):
    """Pairs for a document's translated segments, in position order.

    `origin` and `review` ride along because they answer the reader's next
    question and neither can be re-derived from the report: a segment whose
    origin is `human` is refused to every `llm:*` write by `store.save_targets`
    and dropped from every queue by `cli.do_select`, so no rerun, repair round
    or polish pass will ever reach it. Measured on the maintainer's own project:
    the single misattributed segment in 273 is exactly that segment, which makes
    it the one defect in the whole dataset a person has to repair by hand.
    """
    pairs = []
    for seg in doc["segments"]:
        target = (seg.get("target") or "").strip()
        if not target:
            continue
        pairs.append({"ref": {"seg": seg["id"]},
                      "source": seg.get("source") or "",
                      "target": seg.get("target") or "",
                      "origin": seg.get("origin"),
                      "review": seg.get("review"),
                      "waived": bool(seg.get("waived"))})
    return pairs


def embed_texts(provider, texts, on_progress=None):
    """``(vectors, reasons)`` — one entry per input, either a vector or a reason.

    Batched, and **a batch that fails is re-sent one input at a time**. That is
    `translate.run_batch` falling back to `retry_one`, for the same reason and in
    the same shape: one input a backend will not take must cost that input and
    not its fifteen neighbours. It is also what makes `TOKEN_CEILING` a
    conservative guess rather than a promise — the estimate is calibrated on one
    server's physical batch size, and anything it lets through that the backend
    still refuses is caught here and named.

    **A failure ends the run only while nothing has succeeded yet.** A backend
    answering the wrong shape, or refusing the credential, or not listening at
    all fails on every input alike, and grinding through a whole store one
    request at a time to report two hundred copies of one sentence would be slow,
    rude to the server and a worse answer than the sentence. But once *anything*
    has come back, the backend is answering, and a later failure is about that
    input — so it is recorded and the run continues.

    The condition is deliberately not "every input of this batch failed", which
    is what it was first written as. Two shapes falsify that: a store whose
    length leaves **one** input in the last batch, where a single refusal is the
    whole batch and would have thrown away every vector already computed; and a
    batch that happens to hold sixteen paragraphs all longer than the backend
    takes, which is not rare in a novel. Both were reproduced.
    """
    vectors = [None] * len(texts)
    reasons = [None] * len(texts)
    answered = False
    done = 0
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        try:
            for k, vec in enumerate(provider.embed(chunk)):
                vectors[start + k] = vec
            answered = True
        except ProviderError as batch_error:
            for k, one in enumerate(chunk):
                try:
                    vectors[start + k] = provider.embed([one])[0]
                    answered = True
                except ProviderError as one_error:
                    reasons[start + k] = str(one_error)
            if not answered:
                raise batch_error
        done += len(chunk)
        if on_progress is not None:
            on_progress(done, len(texts))
    return vectors, reasons


def compare(pairs, sources, targets, margin):
    """The suspect pairs, worst first.

    ``sources`` and ``targets`` are unit vectors or ``None``, one per pair. A
    pair is *compared* when both of its own vectors are present; a source that
    is present stays in the rival pool even when its own pair could not be
    compared, because a target may well belong to a record whose own target the
    backend refused.

    **The converse is the blind spot and it is reported rather than fixed**: a
    record dropped before this — an empty source, or one long enough that
    `oversize` declined to offer it — never reaches the pool at all, so a
    misattributed target that belongs to *it* has no rival that can expose it
    and comes back looking clean. `_note` says so with the count beside it,
    because there is nothing to do about it here: the alternative is embedding
    text the backend will refuse.

    **The pool is every other source, and no window narrows it.** A positional
    window was built and measured: at ±10 it lost 2 of the 17 true positives and
    at ±50 it still lost one, whose true owner sat 150 records away in a file
    that holds the same book twice. The saving was not needed either — the
    comparison runs at about 39,000 pairs a second here, so a 3000-record store
    is four minutes and the measured 208-record one is a second. A bound that
    silently reduces recall is the failure this whole command exists to report,
    committed by the command itself.
    """
    rivals = [(i, s) for i, s in enumerate(sources) if s is not None]
    out = []
    for i, pair in enumerate(pairs):
        own_source, target = sources[i], targets[i]
        if own_source is None or target is None:
            continue
        own = cosine(target, own_source)
        best, best_at = -2.0, -1
        for j, other in rivals:
            if j == i:
                continue
            score = cosine(target, other)
            if score > best:
                best, best_at = score, j
        if best_at < 0 or best - own <= margin:
            continue
        rival = pairs[best_at]
        out.append({**pair,
                    "own": round(own, 4),
                    "delta": round(best - own, 4),
                    "belongs_to": {"ref": rival["ref"],
                                   "source": rival["source"],
                                   "score": round(best, 4)}})
    out.sort(key=lambda f: -f["delta"])
    return out


def _note(store, records, compared, skipped, superseded, margin):
    """What the report says about itself, printed whether or not it found anything.

    The third red line of the package this came from: a listing must not imply
    completeness. An empty `flagged` list with no sentence under it reads as a
    clean bill of health, and this instrument cannot issue one — so the counts of
    what it could not look at are in the same paragraph as the count of what it
    found, and the word *clean* appears nowhere in this command's output.

    **It warns against `lx waive`, and the reason is the opposite of the one
    first written here.** The first version said `lx check` reports none of this
    and `lx waive` would therefore refuse. Measured 2026-09-06: `checks.numbers`
    fires at **error** severity whenever the source carries a digit the target
    does not, and a misattributed target is a translation of some other
    sentence, so a chapter heading, a count or a date makes `lx check` exit 1 on
    exactly these segments. `lx waive` then *succeeds* — and a waiver banks
    `"waived": true` into the tracked memory, recording that a reviewer stood by
    the wording. The reviewer the old sentence steered away from waiving was the
    one for whom it would have worked, on the one wording it must not be used on.
    """
    what = "wording(s) in this memory" if store == "memory" else "translated segment(s)"
    lines = [
        f"Compared {compared} of {records} {what} against each other at margin "
        f"{margin:g}. This reports the pairs the embedding separates and certifies "
        f"nothing about the rest: a target that translates none of these sources is "
        f"not what it looks for, and a target whose true source is byte-identical to "
        f"the one it is filed under cannot be told apart by it at all.",
    ]
    if skipped:
        lines.append(f"{skipped} pair(s) were not compared and are listed with the "
                     f"reason. A pair that was not compared is not a pair that came "
                     f"back clean — and its source was not offered as a rival either, "
                     f"so a wording that belongs to one of them cannot be reported "
                     f"against anything.")
    if superseded:
        lines.append(f"{superseded} line(s) of the file carry a wording a later line "
                     f"supersedes. They are not examined, because nothing reads them "
                     f"and appending a correction cannot change them.")
    if store == "memory":
        lines.append("Repair one by re-wording the segment it was banked from and "
                     "running `lx commit` again — the memory only ever grows, and the "
                     "later record wins. `lx audit SRC --lang L` asks the same question "
                     "of a document's own segments, which this did not look at.")
    else:
        lines.append("A segment whose origin is `human` is refused to every model write "
                     "and dropped from every queue, so no rerun reaches it: repair it "
                     "with `lx apply`, or with `lx translate --ids <id> "
                     "--overwrite-human` — naming the id alone reaches the segment and "
                     "is then refused at the write.")
    lines.append("Do not `lx waive` one of these. `lx check` cannot see a "
                 "misattribution as such, but where the source carries a number it "
                 "reports `numbers` at error on exactly these segments — so the waiver "
                 "goes through, and it banks into the tracked memory the claim that a "
                 "reviewer stood by this wording.")
    return " ".join(lines)


def run(provider, lang, doc=None, margin=MARGIN, on_progress=None):
    """The whole audit of one store: the report `cli.do_audit` hands on.

    ``doc`` is a loaded document, or ``None`` for the translation memory.
    """
    if doc is None:
        pairs, superseded = memory_pairs(lang)
        # `store.tm_path`, not a second spelling of where the memory lives.
        store, path, source = "memory", tm_path(lang), None
    else:
        pairs, superseded = document_pairs(doc), 0
        store, path, source = "document", None, doc["source"]

    skipped = []
    live = []
    for pair in pairs:
        # `isinstance` before `.strip()`, because the memory file is
        # hand-editable by design and `store.tm_lines` keeps any line whose
        # `hash` and `target` are merely truthy. A line carrying `"source": 5`
        # used to end the command with an `AttributeError` and exit 1; a
        # `"target": ["…"]` was handed to the backend as a nested array.
        if not isinstance(pair["source"], str) or not isinstance(pair["target"], str):
            skipped.append({"ref": pair["ref"],
                            "reason": "the record's source or target is not text"})
        elif not pair["source"].strip():
            skipped.append({"ref": pair["ref"], "reason": "the record carries no source text"})
        elif oversize(pair["source"]) or oversize(pair["target"]):
            skipped.append({"ref": pair["ref"],
                            "reason": f"longer than the {TOKEN_CEILING}-token ceiling this "
                                      f"command offers a backend; raise the server's "
                                      f"physical batch size to compare it"})
        else:
            live.append(pair)

    dims, vectors = 0, ([], [])
    if live:
        # Sources and targets are two passes over the same records, so progress
        # is reported against their sum rather than twice from zero.
        total = len(live) * 2

        def phase(offset):
            def report(done, _):
                if on_progress is not None:
                    on_progress(offset + done, total)
            return report

        raw_sources, source_why = embed_texts(
            provider, [p["source"] for p in live], phase(0))
        raw_targets, target_why = embed_texts(
            provider, [p["target"] for p in live], phase(len(live)))
        vectors = ([unit(v) if v is not None else None for v in raw_sources],
                   [unit(v) if v is not None else None for v in raw_targets])
        dims = next((len(v) for v in raw_sources + raw_targets if v is not None), 0)
        for i, pair in enumerate(live):
            if vectors[0][i] is None or vectors[1][i] is None:
                why = source_why[i] or target_why[i] or "the endpoint returned an empty vector"
                skipped.append({"ref": pair["ref"], "reason": why})

    sources, targets = vectors
    flagged = compare(live, sources, targets, margin) if live else []
    compared = sum(1 for i in range(len(live))
                   if sources[i] is not None and targets[i] is not None)
    # The pool is every source that embedded, which is not the same as the count
    # of pairs that could be compared: a record whose target the backend refused
    # is still somebody else's rival. Reporting `compared * (compared - 1)`
    # under-stated it, and this is the number the module docstring tells a reader
    # to judge the false-positive risk by.
    pool = sum(1 for v in sources if v is not None)
    return {
        "store": store,
        "lang": lang,
        "path": path,
        "source": source,
        "provider": provider.name,
        "model": provider.model,
        "margin": margin,
        "batch": BATCH,
        "dimensions": dims,
        "records": len(pairs),
        "compared": compared,
        "superseded": superseded,
        "comparisons": compared * max(pool - 1, 0),
        "skipped": skipped,
        "flagged": flagged,
        "note": _note(store, len(pairs), compared, len(skipped), superseded, margin),
    }
