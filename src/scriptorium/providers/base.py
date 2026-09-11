"""Provider interface.

A provider takes a system prompt and a user message and returns text. That is the
entire contract — batching, JSON repair, placeholder validation, and retry of
individual segments all live above this layer in :mod:`scriptorium.translate`,
so adding a backend never means reimplementing the pipeline.
"""

import json
import math
import os
import random
import threading
import time
import unicodedata
from array import array

from ..config import has_version_segment, printable_url
from .errors import ProviderError

# No transport at module scope: `http.client`, `socket` and `urllib` are imported
# inside `Provider._request`, which says why and what fails if that changes.

# Transient by contract: a timeout, a conflict, "too early", a rate limit, and
# the 5xx family a gateway emits while a local runtime is still loading weights.
_RETRYABLE = (408, 409, 425, 429, 500, 502, 503, 504)
_MAX_BACKOFF = 20.0

#: How much of a model id a listing will print. A router's longest real id is
#: about sixty characters; the cap is here because the reply is untrusted and
#: `cmd_models` pads a column to the widest row it was handed. Measured
#: 2026-08-20: one 200k-character `status.value` among 2000 ordinary rows turned
#: a listing into 400 MB of stdout in 0.76 s.
_MAX_FIELD = 120

#: How many rows a listing will carry. `_MAX_FIELD` bounds one field and says
#: nothing about how many there are, which is the other half of the same
#: control: a million 130-byte rows is a bounded field and an unbounded reply,
#: and since 2026-09-01 the audience is a browser reached by changing a dropdown
#: rather than a person who typed `lx models`. The number is chosen against real
#: backends — the development router serves 16 and a large cloud account lists
#: roughly 80 — so nothing selectable is withheld from anyone; a backend that
#: exceeds it has its list cut and `cmd_models` still says when the configured
#: model is not among what came back.
_MAX_ROWS = 1000

#: How many bytes a *listing* may read before it is refused. `_MAX_ROWS` bounds
#: what is serialized back and says nothing about what was parsed to get there:
#: a hostile backend answering 50 MB of rows was measured driving one request
#: thread to roughly 910 MB of peak memory before the cap trimmed the reply, and
#: since 2026-09-01 that request is one dropdown change on a threading server.
#: Four megabytes is far above any real model list — the largest measured is
#: about eighty ids — and the cap is on the listing alone, never on a completion,
#: whose body is legitimately large and is not reachable by a browser gesture in
#: the same way.
_MAX_LIST_BYTES = 4 * 1024 * 1024

#: The Unicode categories a value from a backend may not carry into a terminal
#: or a DOM. `Cc` and `Cf` are the controls and the format characters, which is
#: where the bidirectional overrides live; `Zl` and `Zp` are `U+2028`/`U+2029`,
#: which a great many renderers treat as line breaks.
#:
#: One tuple with two readers — `Provider._sane` drops a listing row, `_tame`
#: scrubs an error body — because they were one rule stated once and enforced in
#: one of the two places it had to be.
_UNSAFE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")

#: A model listing's own budget, bounded below the completion one. See `_get`.
_LIST_TIMEOUT = 30.0
_LIST_RETRIES = 1

#: How many attempts an embedding batch gets, and **the timeout is deliberately
#: not clamped beside it.**
#:
#: A listing is bounded on both because it answers from a table, and its
#: docstring says so. That premise does not transfer: an embedding request runs
#: a model and the first one may load it — `config.DEFAULT_CONFIG` records a
#: 2.5 GB fetch at 104.9 s and puts a 15 GB one at roughly ten minutes, with the
#: router blocking the caller for the whole of it rather than answering
#: something retryable. A ceiling here would be one no configuration could
#: raise, and the sentence the caller then reads — "if the server is simply slow
#: to answer, it is not answering" — would be false of exactly that case. So the
#: `timeout` a project set for this backend is the timeout this uses.
#:
#: `_EMBED_RETRIES` is 1 for a measured reason rather than for symmetry: the
#: per-input size ceiling of a `llama-server` answers **500**, and 500 is in
#: `_RETRYABLE` above — measured 2026-09-06, `input (530 tokens) is too large to
#: process`. That refusal is deterministic, so the shipped `retries: 3` spends
#: four attempts and three backoffs re-asking a question already answered.
_EMBED_RETRIES = 1

#: How many bytes an embeddings reply may be read from. Measured on this machine
#: 2026-09-06 against the bge-m3 server: 21,861 bytes for one 1024-dimension
#: vector, 348,740 for a batch of sixteen and 1,394,722 for a batch of
#: sixty-four — about 21.8 KB a row, and linear.
#:
#: **`_MAX_LIST_BYTES` is not reused, and the reason is scope rather than
#: arithmetic.** Its own comment binds it to a listing, and a constant that
#: means "a listing is never this big" cannot also mean "an embedding batch is
#: never this big" without one of the two sentences becoming untrue the next
#: time either changes. On the numbers alone 4 MiB would in fact have been
#: enough — a batch of sixteen at four thousand dimensions is 1.33 MiB, and it
#: would take about twelve thousand dimensions to reach 4 MiB — and an earlier
#: version of this comment claimed the opposite. Sixteen mebibytes is chosen for
#: the room, not because four is short.
_MAX_EMBED_BYTES = 16 * 1024 * 1024

#: The widest embedding this project will believe. Real models run 384 to 4096;
#: the cap is here because a dimension count decides how much arithmetic the
#: caller then does per pair, so an absurd width is a way to make a read-only
#: command spend the afternoon.
_MAX_EMBED_DIMS = 16384

#: The largest token count this project will believe from a backend. A real
#: completion is bounded by a context window measured in hundreds of thousands,
#: so anything past a trillion is a broken or hostile reply rather than a run
#: that cost a lot. The value is never *shown* — a count outside the range makes
#: the whole reply count as unreported, so the reader is told the number is
#: unknown rather than told a wrong one.
_MAX_TOKENS_REPORTED = 10 ** 12


def _finite(value, cast):
    """`cast(value)`, refusing an infinity or a NaN.

    Both survive `float()` and neither survives `json.dumps` as JSON: they are
    written as the bare tokens `Infinity` and `NaN`, which `JSON.parse` rejects,
    so one hand-edited knob took the whole of `/api/state` down rather than
    marking one row. Raised as `ValueError` because that is what every caller
    here already handles for a knob it cannot read.
    """
    number = cast(value)
    if isinstance(number, float) and not math.isfinite(number):
        raise ValueError(f"{number} is not a finite number")
    return number


def _tame(text):
    """A backend's own prose, safe to put in front of a person.

    Replaced rather than dropped, where `_sane` drops: a listing row that is not
    safe is one of many and withholding it costs nothing, but an error body is
    the whole of what the reader has to go on, and deleting a byte from the
    middle of the server's explanation is worse than showing that something was
    there. `U+FFFD` is what a reader already knows means "not representable".
    """
    return "".join("�" if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch
                   for ch in text)


class _UsageTotals:
    """What a run's completions said they cost, accumulated across threads.

    One of these per `Provider`, and `translate.translate_segments` builds
    exactly one provider per run, so this is a run's total without anything
    having to thread a counter through the batch loop. The lock is not optional:
    `run_batch` and `retry_one` both call `complete()` from
    `batch.concurrency` worker threads.

    **`total` is `prompt + completion`, computed here and never read from the
    reply.** OpenAI's `total_tokens` may legitimately include tokens in neither
    of the other two — cached input on some gateways, reasoning tokens on others
    — and the Anthropic shape has no total at all. Reading it where it exists
    would make one key mean "what the backend said" on one backend and "what we
    added up" on the other, which is the enumeration-as-definition mistake
    `AGENTS.md` records five times.

    `replies` and `reported` are separate on purpose: a floor that reads as a
    total is the one output worse than no output, so a caller can always tell
    "this is what the run cost" from "this is at least what the run cost".
    """

    __slots__ = ("_lock", "prompt", "completion", "replies", "reported")

    def __init__(self):
        self._lock = threading.Lock()
        self.prompt = 0
        self.completion = 0
        self.replies = 0
        self.reported = 0

    def record(self, prompt, completion):
        """One completion reply. ``None`` for either means it reported nothing.

        Both or neither, never one: a reply whose prompt count reads and whose
        completion count is garbage would move one axis while the run still
        called itself fully counted. The caller decides that; this only holds
        the rule that a partial reply is an unreported reply.
        """
        with self._lock:
            self.replies += 1
            if prompt is None or completion is None:
                return
            self.reported += 1
            self.prompt += prompt
            self.completion += completion

    def snapshot(self):
        """A plain dict, copied under the lock so no reader can tear one."""
        with self._lock:
            return {"prompt": self.prompt, "completion": self.completion,
                    "total": self.prompt + self.completion,
                    "replies": self.replies, "reported": self.reported}


class Provider:
    kind = "base"

    #: Where this backend puts the two token counts inside the reply's `usage`
    #: object, as `(prompt, completion)`. The OpenAI spelling is the default
    #: because every OpenAI-compatible runtime uses it; a backend that spells it
    #: differently overrides **only this pair**.
    #:
    #: A two-name tuple and not a method, deliberately. `anthropic.py` records
    #: what happened the last time a rule about an untrusted reply was written
    #: private to `openai_compat.py`: the Anthropic path went unprotected for a
    #: day, because a `kind: "anthropic"` `base_url` is configurable and LiteLLM
    #: serves the Messages API. So reading, validating, bounding and
    #: accumulating all live in this class, where a subclass cannot forget to
    #: call them, and the subclass supplies a fact about its own wire format and
    #: nothing else.
    USAGE_FIELDS = ("prompt_tokens", "completion_tokens")

    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        # Per instance, and `translate_segments` builds one per run, so this is
        # the run's total. Not a class attribute: two providers in one process
        # — `lx run` reaching draft and repair, or a workbench serving two jobs
        # — would share a counter and each would report the other's spend.
        self.usage = _UsageTotals()
        self.model = spec.get("model", "")
        # `_finite`, not a bare `float`. `float("Infinity")` and `float("nan")`
        # both succeed, so a hand-edited `"timeout": "Infinity"` built a provider
        # that never times out — and `providers.available` beside this refuses
        # the same value, so the row said the block could not be read while
        # `build()` handed back a working object. One answer, in both places.
        self.timeout = _finite(spec.get("timeout", 120), float)
        self.temperature = _finite(spec.get("temperature", 0.2), float)
        self.max_tokens = _finite(spec.get("max_tokens", 4096), int)
        self.retries = _finite(spec.get("retries", 3), int)
        # Shape-checked here rather than at the `{**headers, **self.extra_headers}`
        # unpack in `_request`. Both refuse it now, but only this one refuses it
        # at *construction*, which is what makes `providers.build` the single
        # place that answers "can this spec be read" — otherwise a backend the
        # editor just created looks fine until the first run, and the sentence
        # the reader gets is `'str' object is not a mapping`.
        headers = spec.get("headers", {}) or {}
        if not isinstance(headers, dict):
            raise TypeError("`headers` is a block of name to value")
        self.extra_headers = headers
        # Pinned by the first embedding reply and compared against on every one
        # after it. On this class rather than in the caller because a router
        # swapping the resident model mid-run is a fact about the *backend*, and
        # a rule private to one caller is a rule the next caller does not get —
        # `anthropic.py` records the day that cost this project a whole class's
        # protection. `None` until a first reply arrives; nothing else reads it.
        self._embed_dims = None

    # -- credentials -------------------------------------------------------
    @property
    def api_key(self):
        """Read from the environment only.

        Keys are never written to config or state — a project directory that
        gets committed or shared cannot leak one.
        """
        env = self.spec.get("api_key_env") or ""
        return os.environ.get(env, "") if env else ""

    def describe(self):
        # `printable_url`, not the raw value. This line is a display surface —
        # it is the first thing `lx translate` prints and the first entry of the
        # workbench's job log, which `POST /api/job` hands back verbatim — and
        # invariant 6 says every display surface shares one answer about what is
        # printable over a `base_url`. It did not: `lx config get` and
        # `lx providers` masked a hand-edited `https://user:SECRET@host/v1`
        # while this printed it in full, into a log and into an HTTP response.
        # Found by the security-tier re-derivation of the frozen workbench
        # contract, 2026-08-13.
        return f"{self.name} ({self.kind}: {self.model} @ {printable_url(self.spec.get('base_url', ''))})"

    def complete(self, system, user):  # pragma: no cover - interface
        raise NotImplementedError

    def list_models(self):  # pragma: no cover - interface
        """What this backend says it serves: a list of ``{"id", "status"}``.

        ``status`` is ``""`` unless the backend volunteers one. It exists for
        llama.cpp's router, which reports `unloaded` / `loading` / `sleeping` /
        `loaded` per model and is the whole reason this method does — a router
        serving sixteen models selects on an exact id nobody can type from
        memory, so "which ids are there" has to be answerable without leaving
        the tool. Backends that answer a plain OpenAI model list simply have
        nothing to put in the field.

        Advisory, and deliberately so: nothing validates a configured `model`
        against this list. A backend may serve a model it does not enumerate,
        and a list that became a gate would refuse a working configuration on
        the strength of an optional endpoint.
        """
        raise ProviderError(
            f"{self.name}: a {self.kind} backend does not publish a model list here. "
            f"Set the model by hand: `lx config set providers.{self.name}.model <id>`.")

    def embed(self, texts):  # pragma: no cover - interface
        """One vector per input, in the order given: ``[[float]]``.

        The third endpoint, after the completion and the listing, and it is the
        listing's shape rather than the completion's: **advisory, and it gates
        nothing.** `lx audit` reports with it and no other command consults it,
        so a project with no embedding backend loses one report and nothing
        else. It adds no field to the chat-completion body invariant 7 pins —
        the body here is `{model, input}` and a test says so.

        One request, one reply. Batching, the retry of a single input after a
        batch fails, and every threshold live above this layer, exactly as
        `translate.py` lives above `complete()`: what a suspect pair *is* must
        not be decided inside a transport.

        Vectors come back validated — right count, right order, finite numbers,
        one consistent width — and **not normalized**. Whether a zero vector
        ends a run or skips one record is the caller's policy, and a normalizer
        here would have to answer it before the caller could.
        """
        raise ProviderError(
            f"{self.name}: a {self.kind} backend does not serve embeddings here. "
            f"`lx audit` needs an OpenAI-compatible embedding backend — point "
            f"`embedding.provider` at one.")

    # -- listing helpers, shared by every backend that publishes one -------
    @staticmethod
    def _sane(text):
        """Whether a listed id or status is safe to put in front of a person.

        **`lx models` is the one place in this project where text from a remote
        server reaches a terminal**, so a model list is untrusted input. Measured
        2026-08-20 against a hostile mock: an id of
        `evil[2K
TOTALLY-DIFFERENT-MODEL` renders as its second half alone,
        because `[2K` erases the line — so a backend can show one id while
        being another, or wipe out the advisory line printed underneath — and an
        embedded newline forges whole extra rows.

        Categories `Cc` and `Cf` are the controls and the format characters,
        which is where the bidirectional overrides live (`U+202E` reverses the
        display of everything after it). `Zl` and `Zp` — `U+2028` and `U+2029` —
        are here because a *line separator* is a line break to a great many
        renderers, and because the first version of this filter omitted them:
        `json.dumps(..., ensure_ascii=False)` escapes C0 and nothing else, so
        `--json` was never the second line of defence an earlier comment here
        claimed it was. The **drop** is what protects both surfaces.

        Length is part of it too. `cmd_models` pads a column to the widest row it
        was handed, so one 200k-character `status` among 2000 ordinary rows
        produced 400 MB of stdout in 0.76 s.
        """
        return (len(text) <= _MAX_FIELD
                and not any(unicodedata.category(ch) in _UNSAFE_CATEGORIES
                            for ch in text))

    @classmethod
    def _listing(cls, rows):
        """`[{"id", "status"}]` from an OpenAI-shaped `data` array, sorted by id.

        Sorted so that two runs against one backend agree; a router's own order
        is insertion order and does not survive a restart. Rows without a usable
        id, and rows `_sane` refuses, are dropped rather than fatal — a `model`
        value carrying a control character cannot be sent to any of these servers
        anyway, so nothing selectable is being withheld.
        """
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            status = row.get("status")
            if isinstance(status, dict):     # llama.cpp's router: {"value", "args", "preset"}
                status = status.get("value") or ""
            model = {"id": str(row["id"]),
                     "status": str(status) if isinstance(status, str) else ""}
            if all(cls._sane(v) for v in model.values()):
                out.append(model)
        # Sorted before the cut, so which rows survive is a property of the
        # backend's ids rather than of the order it happened to answer in — two
        # runs against one over-long backend agree, which is the same reason the
        # sort is here at all.
        return sorted(out, key=lambda m: m["id"])[:_MAX_ROWS]

    # -- embedding helpers, shared by every backend that serves them -------
    def _vectors(self, data, count, url):
        """``count`` validated vectors from an embeddings reply, in input order.

        In this class and not in `openai_compat.py`, for the reason
        `USAGE_FIELDS` states and `anthropic.py` paid for: a rule about an
        untrusted reply written private to one backend is a rule the second
        backend goes without, and a `kind` is a configurable string.

        **A malformed reply is refused whole; it is never read row by row for
        whatever survives.** That asymmetry is the opposite of `_listing`'s, and
        deliberately so. A dropped listing row costs a model nobody could have
        selected anyway; a dropped vector silently removes a record from the
        comparison the caller is about to make, and a record that was not
        compared is indistinguishable in the answer from a record that came back
        clean. The one exception is left to the caller — see :meth:`embed` on why
        a zero vector is not normalized here.

        The refusals, and what each is for:

        * **The top level is not an object.** This is the measured shape of the
          version-segment mistake: on `llama-server`, `POST /embeddings` and
          `POST /v1/embeddings` are different handlers, and the first answers
          **200** with a bare array whose `embedding` is a list *of lists*
          (measured 2026-09-06). `docs/decisions.md` of 2026-08-20 named an
          embeddings call as the fact that would flip its `base_url` decision,
          on the premise that such a reply would be *silent*. It is not silent
          to a reader that demands an object, which is why this refusal is the
          answer to that note rather than a refusal at write time — see
          `docs/decisions.md`, 2026-09-06.
        * **A row count that differs from the input count.** There is no id in
          this payload to realign by, so a reply that answers a different number
          of questions does not answer this request. It is
          `translate.misattributed`'s rule one layer down, and the same
          treatment: thrown away whole.
        * **`index` that is not a permutation of `range(count)`.** Rows are
          *placed* by `index`, never taken in arrival order. This endpoint was
          measured answering in order on one build, and a measurement on one
          build is not a licence to ignore the field the specification says is
          the answer's address — an instrument built to find misattribution must
          not be able to misattribute.
        * **A non-numeric, boolean or non-finite element.** `json.loads` accepts
          the bare tokens `NaN` and `Infinity` as an extension, and a NaN does
          not raise anywhere downstream — it makes every comparison false, so a
          poisoned reply would report a clean store. `bool` is refused for
          `_token_count`'s reason: `isinstance(True, int)` is true.
        * **A width that disagrees**, within the reply or with an earlier reply
          of this run. The second half is what a single-batch check cannot see:
          the development backend is a router serving sixteen models, and one
          that swaps the resident model between batches would hand back two
          incomparable geometries with nothing in either reply saying so.

        **One refusal below prints part of the reply and every other one prints
        only a type name**, which is the opposite of what an earlier version of
        this paragraph claimed. The exception is the first: a reader whose
        backend answered the wrong shape needs to see some of it, so
        `str(data)[:300]` goes through `_tame` — and `_tame` is what makes that
        safe, not `repr`. A reply whose top level is a JSON *string* reaches the
        message unquoted, because `str` of a string is the string; the control
        characters and the bidirectional overrides are gone either way, which is
        the property that matters. Everything after it names a type and nothing
        else, so there is no second place for a backend's own text to arrive.
        """
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderError(
                f"{self.name}: {printable_url(url)} did not answer an embeddings list "
                f"(expected a `data` array, got {type(data).__name__}): "
                f"{_tame(str(data)[:300])}{self._url_hint(None, url)}")
        if len(rows) != count:
            raise ProviderError(
                f"{self.name}: asked {printable_url(url)} for {count} embedding(s) and "
                f"it answered {len(rows)}. Nothing in the reply says which input each "
                f"row belongs to, so none of it is used.")
        out = [None] * count
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError(
                    f"{self.name}: {printable_url(url)} answered a row that is not an "
                    f"object ({type(row).__name__}).")
            idx = row.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < count:
                raise ProviderError(
                    f"{self.name}: {printable_url(url)} answered a row whose `index` is "
                    f"not a position in this request.")
            if out[idx] is not None:
                raise ProviderError(
                    f"{self.name}: {printable_url(url)} answered `index` {idx} twice, so "
                    f"at least one input has no vector and one has two.")
            out[idx] = self._vector(row.get("embedding"), url)
        width = len(out[0])
        if any(len(v) != width for v in out):
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered vectors of different "
                f"widths in one reply, which cannot be compared with each other.")
        if self._embed_dims is None:
            self._embed_dims = width
        elif width != self._embed_dims:
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered {width}-dimension vectors "
                f"where an earlier request of this run got {self._embed_dims}. The "
                f"backend changed model mid-run and the two cannot be compared.")
        return out

    def _vector(self, value, url):
        """One row's `embedding` as an ``array('f')``, or a `ProviderError`.

        `array('f')` rather than a list, and it is a size decision rather than a
        style one: a list of Python floats costs about 32 KB per 1024-dimension
        vector against 4 KB here, measured, so a ten-thousand-record memory is
        640 MB one way and 80 MB the other. The arithmetic that reads it is
        `sum(map(mul, a, b))`, measured about 1.9 times faster than a generator
        over `zip` and about 22% slower on arrays than on lists — a price worth
        paying once, since the alternative is a command that cannot finish a book
        at all. Single precision costs well under 1e-7 of a cosine, three orders
        below this instrument's own measured reproducibility.

        **Finiteness is checked on the array and not on the reply**, and the
        first version of this had it the other way round with a comment claiming
        a guard it did not have. Two measurements, 2026-09-06:

        * `array("f", [1e300])[0]` is `inf`. It does **not** raise — so a reply
          holding a perfectly finite double that single precision cannot store
          passed `math.isfinite`, became an infinity here, made `audit.unit`
          return a vector of `NaN`, and every comparison against it false. The
          command would have reported a **clean store over a poisoned one**,
          which is the one output it exists not to produce, and `--json` would
          have carried a bare `NaN` that `JSON.parse` refuses.
        * `math.isfinite(10 ** 400)` **raises** `OverflowError`. A 401-digit
          integer is far inside `json.loads`' own 4300-digit limit, so a reply
          could reach that call and leave as an exception that is not a
          `ProviderError`, is not in `cli.main`'s exit-2 tuple, and skips
          `audit.embed_texts`' per-input fallback entirely: a traceback and
          exit 1.

        Building the array first and asking `math.isfinite` of what came out
        answers both, because every way a number can fail to be storable ends as
        `inf` or `nan` there — and the one that does not, the huge integer, is
        the one `array` itself refuses.
        """
        if not isinstance(value, list) or not value:
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered a row whose `embedding` is "
                f"not a non-empty array ({type(value).__name__}).")
        if isinstance(value[0], list):
            # The nested form, and it gets its own sentence because it is the one
            # wrong shape with a known cause: `llama-server` serves `/embeddings`
            # and `/v1/embeddings` from different handlers and the first answers
            # a list of lists. `_vectors` refuses that reply at the top level
            # before this is reached, so what arrives here is the nested body
            # behind a gateway that wrapped it — still the same diagnosis, and
            # `_url_hint` keeps it silent where the path is not the cause.
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered a row whose `embedding` is "
                f"an array of arrays, which is what llama.cpp's own `/embeddings` "
                f"handler returns.{self._url_hint(None, url)}")
        if len(value) > _MAX_EMBED_DIMS:
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered a {len(value)}-dimension "
                f"vector, which no real embedding model serves.")
        for x in value:
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                raise ProviderError(
                    f"{self.name}: {printable_url(url)} answered a vector holding "
                    f"{type(x).__name__}, not numbers.")
        try:
            vec = array("f", value)
        except OverflowError:
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered a vector holding a whole "
                f"number too large to be a coordinate.") from None
        if not all(map(math.isfinite, vec)):
            raise ProviderError(
                f"{self.name}: {printable_url(url)} answered a vector holding a value "
                f"that is not a finite number this project can store. `json.loads` takes "
                f"the bare tokens NaN and Infinity, and a value merely too large for "
                f"single precision becomes one — none of them raise anywhere "
                f"downstream, they make every comparison false, so a poisoned reply "
                f"would report a clean store.")
        return vec

    # -- transport ---------------------------------------------------------
    def _backoff(self, attempt, retry_after=None):
        """How long to wait before the next attempt.

        `Retry-After` wins when the server sends a number, because a hosted API
        returning 429 knows its own window and an exponential guess does not.
        Its HTTP-date spelling is deliberately not honoured: parsing a date to
        wait on it is more machinery than this case earns, and falling through
        to our own backoff is never wrong, only slower.

        Jitter because a batch runs several requests concurrently against one
        server. Without it every one of them fails together and then retries in
        the same instant, which is the burst that caused the failure arriving
        again on schedule.
        """
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), _MAX_BACKOFF)
            except ValueError:
                pass  # an HTTP-date; use the backoff we control
        return min(2 ** attempt + random.uniform(0, 1), _MAX_BACKOFF)

    def _url_hint(self, code, url):
        """The one sentence that names the commonest `base_url` mistake, or `""`.

        Two things about it were decided on 2026-08-20 and each closes a measured
        failure.

        **It is on the 404 path at all.** The advice used to live only on the
        `URLError` branch — which is *cannot connect* — while the symptom of a
        `base_url` missing its version segment is an `HTTPError` 404 from a
        server that answered perfectly well. So the message reached everyone
        except the person who needed it. Reproduced against Ollama's own routing,
        and it is the failure vscode#296859 records the Copilot team hitting.

        **It says "version segment", not "/v1", and only when there is none.**
        The old sentence was unconditional and prescriptive: Continue.dev shipped
        that shape and told a user whose endpoint was `/v2` that they had
        forgotten `/v1` (issue #7682). `/api/v1`, `/openai/v1/` and `/v1beta` are
        all ordinary, and `has_version_segment` is silent on every one of them.

        It never fires on a 401, 400 or 5xx: those mean the route was found, and
        a hint about the path would be a lie with a plausible ring to it.

        **A 200 of the wrong shape is the case that sentence did not cover**, and
        it is why `_vectors` passes `None` here. `llama-server` serves
        `/embeddings` and `/v1/embeddings` from different handlers and the first
        answers 200 with a bare array (measured 2026-09-06) — the route was
        found, and the path is still exactly what is wrong. The `code is None`
        branch already says "we cannot tell you a status code, only that the URL
        has no version segment", which is true of that reply as much as of a
        connection that never opened.
        """
        if code is not None and code != 404:
            return ""
        if has_version_segment(url):
            return ""
        # It deliberately does **not** end by recommending `lx models`. That was
        # the first draft, and `lx models` is itself one of the callers — so the
        # failure of that very command ended by advising the reader to run it.
        # Naming a remedy is `cmd_config_set`'s job, where the command being
        # named is not the one that just failed. ("One of the two" is what this
        # said until 2026-09-06; `_vectors` and `_vector` are the third and
        # fourth, and an enumeration in a comment is the thing this project has
        # watched go stale six times.)
        return (" That path carries no API version segment — many endpoints serve "
                "this API under one, as /v1.")

    @staticmethod
    def _token_count(value):
        """One token count from an untrusted reply, or ``None``.

        `int` and not `bool`, which is the `cli._int` rule and is load-bearing
        rather than pedantic: `isinstance(True, int)` is true, so a backend
        answering `{"prompt_tokens": true}` would otherwise add 1 to a total and
        call the reply counted.

        **A float is refused outright, `42.0` included.** Accepting floats means
        accepting `NaN` and `Infinity`, which `json.loads` produces from the bare
        tokens it takes as an extension and `json.dumps` writes back as those
        same bare tokens — invalid JSON, and precisely the shape `_finite`
        records taking `/api/state` down from a hand-edited config. Here it
        would arrive from the wire instead, which is a strictly worse source.

        The upper bound catches the rest. A 4300-digit integer literal never
        reaches this function — `json.loads` itself raises on one, inside the
        handler in `_request` that already turns that into a `ProviderError` —
        so what is left is a merely absurd number, and an absurd number that is
        shown is worse than one that is refused.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not 0 <= value <= _MAX_TOKENS_REPORTED:
            return None
        return value

    def _record_usage(self, data):
        """Count one completion reply into this run's totals.

        Nothing from `data` is ever formatted as text by this path — only the
        two integers that survive `_token_count` are, and only after they are
        added up. That absence is the property rather than an oversight: `_sane`
        and `_tame` exist because a listing row and an error body *are* printed,
        and a rule that has no string to escape needs no escaper.
        """
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            return self.usage.record(None, None)
        prompt_key, completion_key = self.USAGE_FIELDS
        prompt = self._token_count(usage.get(prompt_key))
        completion = self._token_count(usage.get(completion_key))
        self.usage.record(prompt, completion)

    def _post(self, url, payload, headers):
        """A completion, with the caller's own `timeout` and `retries`."""
        # `method` is explicit rather than inferred from `payload is not None`.
        # It was inferred, and `_post(url, None, headers)` therefore issued a
        # silent GET — unreachable from the two callers that exist today and a
        # trap set for the third. A POST with a JSON `null` body is a strange
        # thing to want, but it is not a GET.
        data = self._request(url, headers, payload=payload, method="POST")
        # Here rather than in each `complete()`, and that is the whole reason
        # this counter is trustworthy: `_post`'s two callers are exactly the two
        # `complete()` implementations, so a reply cannot be counted twice and a
        # third backend is counted without its author knowing this exists.
        # `_get` deliberately does not do this — a model listing is not a
        # completion and has no cost to report.
        self._record_usage(data)
        return data

    def _get(self, url, headers):
        """A read, through the same retry, backoff and masking as a completion.

        Written as a second entry point into `_request` rather than as its own
        loop, because everything that makes `_post` careful — `Retry-After`, the
        jitter, the no-sleep-on-the-final-attempt rule, the three error messages
        that go through `printable_url` — is exactly as necessary for a read
        against a local server that may be mid-load. A second copy would be a
        second place to forget one of them.

        **What it does not share is the budget.** `timeout` and `retries` are
        sized for a *completion*, and on a llama.cpp router that means 600
        seconds, because the first request for a model may load and even download
        it. A listing never incurs that: `GET /models` answers from a table. With
        the shipped `llamacpp` entry the two together are 600 x 4 = **40 minutes**
        before a black-holed listing gives up — measured 2026-08-20 — for a
        command `docs/windows-setup.md` sells as the quick way to check that the
        server is answering at all. So it is bounded here, and only downward: a
        project that deliberately set a *shorter* timeout keeps it.
        """
        return self._request(url, headers, payload=None, method="GET",
                             timeout=min(self.timeout, _LIST_TIMEOUT),
                             retries=min(self.retries, _LIST_RETRIES),
                             max_bytes=_MAX_LIST_BYTES, what="a model listing",
                             advice=" A model listing is bounded well below the "
                                    "completion timeout on purpose; if the server is "
                                    "simply slow to answer, it is not answering.")

    def _embed_post(self, url, payload, headers):
        """A batch of embeddings: a POST with a read's budget.

        The third entry point into `_request`, written as `_get`'s sibling and
        for its stated reason — everything that makes this loop careful, from
        `Retry-After` to the `InvalidURL` mask invariant 6 gained on 2026-09-01,
        is exactly as necessary here, and a second copy is a second place to
        forget one of them.

        **It is a POST and it is not `_post`.** `_post` records usage, and its
        docstring says in terms that its two callers are exactly the two
        `complete()` implementations — which is what makes that counter
        trustworthy. Routing an embedding through it would not merely be
        inaccurate, it would falsify a documented property: the measured reply
        carries `prompt_tokens` and `total_tokens` and **no**
        `completion_tokens`, so `_UsageTotals.record` takes its
        one-of-two-present branch and counts every reply as *unreported* — a run
        would report replies climbing while reported stayed at zero, on a
        command that bought no completions at all. `_get` set the precedent for
        the right move: a second door, and "a model listing is not a completion
        and has no cost to report". Neither is this. What the audit reports
        instead is what it counted itself, and the reply's `usage` is read by
        nothing.

        **Retries are clamped and the timeout is not**, which is where this
        parts company with `_get` — see `_EMBED_RETRIES`. The reply is bounded
        in bytes because it is a read a person can start by typing one command,
        and a hostile one is otherwise parsed in full.
        """
        return self._request(url, headers, payload=payload, method="POST",
                             retries=min(self.retries, _EMBED_RETRIES),
                             max_bytes=_MAX_EMBED_BYTES,
                             what="a batch of embeddings",
                             advice=" The first request to a backend may be loading the "
                                    "model — raise `providers.<name>.timeout`, or load "
                                    "the model before auditing.")

    def _request(self, url, headers, payload=None, method="POST",
                 timeout=None, retries=None, max_bytes=None, what=None,
                 advice=None):
        # **Only http(s) leaves this function.** `urllib`'s stock opener also
        # speaks `file:`, `ftp:` and `data:`, so a hand-edited `base_url` of
        # `file:///…` made the one endpoint a browser gesture can reach into a
        # local-file read whose content comes back inside the wrong-shape error
        # message. No configuration this project writes can produce one —
        # `_field_base_url` refuses an empty netloc — so this costs nothing and
        # closes the hand-edited case. A plain prefix test rather than
        # `urlsplit`, because that parser raises on some of the inputs this is
        # here to refuse.
        if not str(url).lower().startswith(("http://", "https://")):
            raise ProviderError(
                f"{self.name}: base_url must be an http:// or https:// address. This one "
                f"names another scheme, and the value is not repeated here.")
        # **The transport is imported here, not at module scope.** Every `lx`
        # command executes this module — `cli.py` binds `ProviderError` at module
        # scope, and Python runs `providers/__init__.py`, which imports this file
        # to build `KINDS`, before it binds any submodule — while these four
        # names are used in this function and nowhere else, and only once a
        # request is being sent. At module scope `urllib.request` brought `ssl`,
        # `http.client`, `socket` and the `email` package with fourteen of its
        # submodules into `lx --help`, on 3.9 through 3.12. Measured 2026-09-11
        # on 3.12, median of seven warm runs: `import scriptorium.cli` took 69 ms
        # with them there and 44 ms with them here.
        # `tests/test_startup_imports.py` fails if any of the four moves back.
        #
        # **Here and not in `Provider.__init__`**, which would run the first
        # import on the thread that builds the provider — `translate_segments`
        # builds one before it starts its pool — but could not bind these names
        # for this function without a `global`, so the statements would be here
        # as well: two sites for one fact, bought only to keep the first import
        # off the worker threads. The import system's per-module lock already
        # serializes a concurrent first import. Measured 2026-09-11: 32 threads
        # released by one `Barrier` into this function against a closed port, in
        # a fresh interpreter, 50 times each on 3.9 and 3.12 — every thread ended
        # in the `URLError` branch below, none in an `ImportError`, an
        # `AttributeError` or a partially initialized module. After the first
        # call the four statements are `sys.modules` lookups.
        import http.client
        import socket
        import urllib.error
        import urllib.request

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        last = None
        for attempt in range(retries + 1):
            # Every sleep below is guarded by this. The loop used to wait after
            # its final attempt and then leave and raise anyway, so `retries=0`
            # cost a second of pure latency per failed call — which the suite
            # paid on every run.
            final = attempt == retries
            try:
                # **Constructed inside the `try`, and this is depth rather than
                # the guard.** `Request.__init__` raises `ValueError("unknown url
                # type: %r")` quoting the *whole* URL, userinfo and all, and it
                # sat above this block where the masked handler below could not
                # see it — a `base_url` of `//user:SECRET@host/v1` was masked by
                # `/api/state` and printed in full by `GET /api/models` beside
                # it. Measured 2026-09-01.
                #
                # **It has no reachable case today**, and that is worth saying
                # rather than leaving for somebody to rediscover: the scheme
                # check at the top of this function refuses the only URL form
                # `Request.__init__` rejects, so it fires first. A mutation run
                # proved it — moving this construction back out left the suite
                # green, and the test that claimed to pin it was passing on the
                # scheme check instead. It stays because the two guards answer to
                # different rules, and the day the scheme rule widens this is
                # what is left.
                req = urllib.request.Request(url, data=body, method=method)
                for k, v in {**headers, **self.extra_headers}.items():
                    req.add_header(k, v)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    # Bounded for a listing, unbounded for a completion. One
                    # extra byte is read so that "exactly at the cap" and "over
                    # it" are distinguishable without a second call.
                    raw = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
                if max_bytes is not None and len(raw) > max_bytes:
                    # `what` rather than a hard-coded "a model list". The listing
                    # was this branch's only caller for a year and the sentence
                    # said so; the moment a second bounded read existed, that
                    # sentence became a wrong one the new caller inherited
                    # silently. Found by the security-tier pass over this change.
                    raise ProviderError(
                        f"{self.name}: {printable_url(url)} answered more than "
                        f"{max_bytes} bytes for {what or 'this request'}, which no real "
                        f"backend does. Nothing was parsed.")
                try:
                    return json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    # A 200 that is not JSON. Outside the three handlers
                    # below, `json.loads` raised straight through them and
                    # out of `cli.main`, which has no `ValueError` in its
                    # exit-2 tuple: a traceback and exit 1. Every other
                    # caller was shielded by `translate.run_batch`'s blanket
                    # `except Exception`; `cli.do_models` is not, and the
                    # trigger is the very misconfiguration `_url_hint` was
                    # added for — a proxy or a web UI at the root answering
                    # HTML. OpenRouter's bare host answers 200 with 131 KB
                    # of it. Raised here rather than retried: a server that
                    # answered the wrong content type will answer it again.
                    raise ProviderError(
                        f"{self.name}: {printable_url(url)} answered {resp.status if hasattr(resp, 'status') else 200} "
                        f"but not JSON ({e}). Check that base_url points at the API "
                        f"rather than at a web page."
                        f"{self._url_hint(404, url)}") from e
            except urllib.error.HTTPError as e:
                # `_tame`, not a bare slice. This body is the backend's own bytes
                # and it is the **only** interpolation on this path that reaches a
                # reader unescaped: `{name!r}` and `str(data)[:300]` beside it go
                # through `repr`, which turns an ESC or a `U+202E` into a literal
                # `\x1b` / `‮`, and this one did not. Measured 2026-09-01 by
                # the security-tier pass over `GET /api/models`: a 4xx body of
                # `x\x1b[2Ky` erases the line it is printed on, and a `U+202E`
                # reverses the display of everything after it — in a terminal for
                # `lx models`, and now in a browser, where `textContent` stops
                # markup and does nothing about a bidirectional override.
                #
                # It is the same rule `_sane` states for a listing row, applied
                # where the enumeration missed: `_sane` filters `id` and `status`
                # and never touched an *error* body, so the docstring's claim that
                # the drop protects both surfaces was true of the rows alone.
                detail = _tame(e.read().decode("utf-8", "replace")[:500])
                last = ProviderError(
                    f"{self.name}: HTTP {e.code} — {detail}{self._url_hint(e.code, url)}")
                if e.code not in _RETRYABLE:
                    raise last from e
                if not final:
                    time.sleep(self._backoff(attempt, e.headers.get("Retry-After")))
            except urllib.error.URLError as e:
                last = ProviderError(
                    # Masked for the same reason `describe` is: this message
                    # reaches `/api/job`'s `error` field, and a URL is the one
                    # place a credential hides in something nobody thinks of as
                    # a credential. `printable_url` keeps scheme, host and path,
                    # so the version-segment advice still reads.
                    #
                    # The sentence still names `base_url`; only the *prescriptive*
                    # half moved into `_url_hint`. The field is the thing to go
                    # and look at whichever way the URL is wrong — "it must end
                    # in /v1" was the part that was sometimes false.
                    f"{self.name}: cannot reach {printable_url(url)} — {e.reason}. "
                    f"For a local server, check that it is running and that "
                    f"base_url names the right host and port."
                    f"{self._url_hint(None, url)}")
                if not final:
                    time.sleep(self._backoff(attempt))
            # `socket.timeout` only became an alias of the builtin in 3.10, and
            # 3.9 is the declared floor and a CI matrix entry. There a stalled
            # read — headers received, body never arriving — raises the socket
            # class, which is not a `TimeoutError` and not a `URLError` either,
            # so it escaped both handlers and reached the user as a bare OSError
            # instead of the message below. On 3.10+ the two names are one class
            # and the tuple is a duplicate, which costs nothing.
            except (TypeError, ValueError, http.client.HTTPException) as e:
                # A URL `urllib` will not even attempt, and every other failure
                # this client raises on its own rather than as an `OSError`. No
                # exchange happened, so no retry can help and this raises rather
                # than setting `last`.
                #
                # **The class, not the member.** `http.client.InvalidURL` is the
                # one that was measured, and it is neither a `ValueError` nor an
                # `OSError` — it descends from `HTTPException`, so `urllib` does
                # not wrap it in a `URLError` and the first version of this guard,
                # written against `ValueError` alone, did not catch it. Naming the
                # exact subclass would have been the same mistake one level down.
                #
                # It is here for one reason: **the exception's own message quotes
                # the part it choked on**, and for a hand-edited
                # `https://user:SECRET@host/v1` that part is `SECRET@host`.
                # Measured 2026-09-01, by the probe over the new
                # `GET /api/models`: the endpoint answered
                # `400 {"error": "nonnumeric port: 'SECRET@host'"}` — a password in
                # an HTTP response body a browser renders — and `lx models` and
                # `lx translate` answered a traceback carrying the same string
                # before that.
                #
                # `ValueError` is not `URLError`, so none of the three masked
                # messages in this loop applied to it, and it is not in
                # `cli.main`'s exit-2 tuple either. That is invariant 6's own
                # clause rather than a new rule: the enumerated list of display
                # surfaces is a symptom and never the definition, and this was a
                # fourth surface nobody had counted. `from None` because the
                # chained original carries the same value into a traceback.
                raise ProviderError(
                    f"{self.name}: {printable_url(url)} could not be requested "
                    f"({type(e).__name__}). Check `base_url` — and a credential belongs in "
                    f"the environment variable named by `api_key_env`, never in the URL."
                ) from None
            except (TimeoutError, socket.timeout):
                # The caller's own sentence, not one derived from `method`. It
                # branched on `method == "GET"`, which meant "is this the
                # listing" only for as long as the listing was the one
                # non-completion call — the first POST that was not a completion
                # inherited advice to lower `batch.size`, a knob with nothing to
                # do with it. Deriving it from `what` instead was the first
                # repair and was still wrong: it gave an embedding batch the
                # listing's "it is not answering", which is false of a backend
                # loading a model. Each caller states its own remedy because each
                # caller has a different one.
                last = ProviderError(
                    f"{self.name}: timed out after {timeout}s."
                    + (advice or " Local models on CPU are slow — raise `timeout` or "
                                 "lower `batch.size`."))
                if not final:
                    time.sleep(self._backoff(attempt))
        raise last or ProviderError(f"{self.name}: request failed")
