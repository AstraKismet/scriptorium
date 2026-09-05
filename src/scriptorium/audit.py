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
  whose source merely appears twice flags freely — 89 of 208 records in the
  measured file have a duplicated source and 16 of the 17 true positives are
  among them. Only the rival that would expose it is silenced.
* **Its own false-positive rate at a size it has not been measured at.** The
  score is a maximum over every other record, so it can only grow with the
  store: subsampling the measured file, the worst delta over records known clean
  runs +0.0002 at 25 records and +0.0700 at 208, against a lowest true positive
  of +0.1166. The margin below was chosen on 208 records and is not validated
  for thousands. `own` and `delta` ride on every finding so a reader can see how
  near the edge each one sat.

See `docs/decisions.md`, 2026-09-06, for the measurements and for the two rules
that were built, scored and removed.
"""

import unicodedata
from array import array
from operator import mul

# `providers.errors`, not `providers`. That module exists so that naming this
# class costs nothing: importing `providers` pulls `urllib.request` and with it
# `ssl`, `http.client`, `socket` and fifteen `email` submodules, measured at
# roughly doubling the cost of importing `scriptorium.cli`. This module is
# imported by `cli` for one command and must not put that on `lx --help`.
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
#: without this the refusal is retried before it is believed. 460 leaves a tenth
#: of that in hand.
#:
#: There is no tokenizer here and there will not be one: invariant 1 forbids a
#: compiled dependency, and a pure-Python SentencePiece is a model file, not a
#: rule. So the estimate is on characters, from two measured ratios — 0.85
#: tokens per character for Traditional Chinese, 0.24 for English — with the
#: second rounded **up** to 0.30. The asymmetry is chosen: it over-skips English
#: prose by about a quarter rather than under-skipping CJK, because an
#: over-skipped record is reported as skipped and an under-skipped one costs a
#: request that fails.
TOKEN_CEILING = 460

_DENSE_PER_CHAR = 0.85
_OTHER_PER_CHAR = 0.30


def estimated_tokens(text):
    """A deliberately high guess at how many tokens ``text`` will cost.

    "Dense" is Unicode category ``Lo`` — a letter with no case — which is CJK,
    kana, Hangul, Thai and the abugidas. **Not `mask.CJK_RE`**, which is the
    right answer to a different question: that range is pinned to where
    `mdparse` puts segment boundaries and its own comment names the exclusion of
    kana and Hangul as a real limitation. Borrowing it here would inherit a
    limitation about block structure to answer a question about token density.
    """
    dense = sum(1 for ch in text if unicodedata.category(ch) == "Lo")
    return _DENSE_PER_CHAR * dense + _OTHER_PER_CHAR * (len(text) - dense)


def oversize(text):
    """Whether this input is too long for a backend to be offered it."""
    return estimated_tokens(text) > TOKEN_CEILING


def unit(vec):
    """``vec`` scaled to length one, or ``None`` if it has no length.

    A zero vector is the one reply defect this module survives per record rather
    than refusing whole: one degenerate input does not condemn a run, and the
    record it belongs to is reported as not compared. Everything else the
    backend can get wrong is `providers.base.Provider._vectors`' business and is
    refused there.

    Vectors were measured arriving normalized to within 2e-8, and this is done
    anyway — a measurement of one model is not a property of the endpoint.
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

    **A failure that reaches every input of a batch ends the run** by re-raising.
    A backend answering the wrong shape, or refusing the credential, or not
    listening at all fails on every input alike, and grinding through the whole
    store one request at a time to report two hundred copies of one sentence
    would be slow, rude to the server, and a worse answer than the sentence
    itself. A size refusal never has that shape, because the inputs that are too
    long are the long ones.
    """
    vectors = [None] * len(texts)
    reasons = [None] * len(texts)
    done = 0
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        try:
            for k, vec in enumerate(provider.embed(chunk)):
                vectors[start + k] = vec
        except ProviderError as batch_error:
            got = 0
            for k, one in enumerate(chunk):
                try:
                    vectors[start + k] = provider.embed([one])[0]
                    got += 1
                except ProviderError as one_error:
                    reasons[start + k] = str(one_error)
            if not got:
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

    It also names the commands that compose, because the obvious one does not:
    `lx waive` refuses a segment `lx check` reports nothing on, and `lx check`
    reports nothing on any of these — a fluent sentence that translates the
    wrong source breaks no mechanical rule. A reviewer whose first move is to
    waive gets a refusal and no explanation of it.
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
                     f"reason; a pair that was not compared is not a pair that came "
                     f"back clean.")
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
                     "with `lx apply` or `lx translate --ids`. `lx check` reports none "
                     "of this and `lx waive` will refuse a segment it reports nothing "
                     "on.")
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
        if not pair["source"].strip():
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
        "comparisons": compared * max(compared - 1, 0),
        "skipped": skipped,
        "flagged": flagged,
        "note": _note(store, len(pairs), compared, len(skipped), superseded, margin),
    }
