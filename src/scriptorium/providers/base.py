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
import re
import threading
import time
import unicodedata
import urllib.parse
from array import array

from ..config import has_version_segment, printable_url
from .errors import ProviderError

# No transport at module scope: `http.client`, `socket`, `urllib.request` and
# `urllib.error` are imported inside `Provider._request`, which says why and
# what fails if that changes. `urllib.parse` is not the transport — `config`
# already loads it for `printable_url` — and `_spellings` needs its `quote`.

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

#: The shortest value the redaction removes whole. Below it a value is a
#: placeholder by every local runtime's own convention — `EMPTY` is 5,
#: `ollama` 6, `sk-1234` 7 — and replacing one would delete ordinary characters
#: from the backend's sentence, which is the one thing the package's red lines
#: forbid a floor to do. `password` and `lm-studio` are removed wherever they
#: appear whole; a reader who sees that learns their key is a word, which is
#: worth knowing. A value shorter than this is never redacted and **no message
#: says so**: a note would state a length class of the key.
_WHOLE = 8

#: The shortest *proper* run of a value the redaction removes — what a backend
#: that cut the value, wrapped it, or masked all but its head still shows. The
#: false-positive population of a piece rule is ordinary words inside a
#: placeholder key, `required` inside `sk-no-key-required`, and twelve clears
#: them. OpenAI's own mask `sk-proj-****abcd` is left as the backend wrote it:
#: `sk-proj-` is eight and `abcd` is four.
#:
#: **The guarantee is per run, not per key**: no contiguous run of twelve or
#: more of a value survives. A backend that prints the key in groups of eleven
#: with a separator between shows all of it, eleven characters at a time
#: (measured). That is a designed exposure and not a defect — such a backend
#: already holds the key, and a rule that chased separators would have to
#: remove ordinary text — and it is stated here so that nobody reads "at most
#: eleven" as a bound on the key.
_PIECE = 12

#: What the redaction writes in a value's place. ASCII, so `_tame` passes it;
#: no `"` or `\`, so a JSON body it lands in stays well-formed; no `⟦`/`⟧`;
#: fixed text, so it says nothing about the value's length. **One marker for
#: every source**: the `Authorization` value contains the key, so a per-source
#: label would have to choose between overlapping sources, and the sentence the
#: backend wrote already says what was echoed.
_MARKER = "[credential redacted]"

#: The shortest run of a credential that refuses a 200 reply whole: a window
#: of this many consecutive characters of any spelling of a gated value (see
#: `_gated`), not the whole spelling — a completion quoting the first
#: twenty-four characters of a key was measured accepted, and banked. A value
#: is gated only when it is itself this long: `sk-no-key-required` — the
#: placeholder llama.cpp's own examples use, eighteen characters — appears
#: legitimately in the technical documents this project also translates, and
#: a user-chosen key on a self-hosted gateway can be shorter than this too;
#: such a key is not gated, and the error path still redacts it from
#: `_WHOLE`. Every hosted key is at least thirty-two. Windows of this length
#: and not of `_PIECE`, because a key made of words (`translation-server-key`)
#: has twelve-character pieces that occur in ordinary prose, and a refused
#: reply is a lost translation where an over-redacted error costs a glance.
_GATE = 20

#: How much of an error body is read. Bounded where `e.read()` was not, and
#: sized so the redaction window is larger than the display window: any run of
#: a value that starts inside the 500 characters `_request` shows ends inside
#: this.
_ERROR_BODY_BYTES = 64 * 1024

#: The pieces of a spelling that `_spellings` rewrites: a `%XX` pair, for the
#: lower-case percent form; a `\\uXXXX` escape, for the upper-case hex .NET
#: writes; and the five characters Go's and .NET's JSON encoders escape by
#: default that Python's does not.
_PERCENT = re.compile(r"%[0-9A-F]{2}")
_JSON_HEX = re.compile(r"\\u([0-9a-f]{4})")
_JSON_EXTRA = re.compile(r"[<>&'+]")


def _percent_lower(m):
    return m.group(0).lower()


def _json_upper(m):
    return "\\u" + m.group(1).upper()


def _json_escape(m):
    return f"\\u{ord(m.group(0)):04x}"


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

    It runs **after** `Provider._redact` and never before it — `_refusal` and
    `_excerpt` hold the order — so it can never change a credential's spelling
    under the match, and the marker it is handed is ASCII and passes through.
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
        # What the transport added to a request of this provider's on its
        # own, accumulated for its life — see `_note_transport`. A tuple
        # replaced under the lock and never mutated, so a reader takes the
        # old one or the new one; and `_tables`' memo beside it, the same
        # shape for the same reason.
        self._transport_added = ()
        self._transport_lock = threading.Lock()
        self._scan_memo = None

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
        raise self._refusal(
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
        raise self._refusal(
            f"{self.name}: a {self.kind} backend does not serve embeddings here. "
            f"`lx audit` needs an OpenAI-compatible embedding backend — point "
            f"`embedding.provider` at one.")

    # -- listing helpers, shared by every backend that publishes one -------
    @staticmethod
    def _sane(text):
        """Whether a listed id or status is safe to put in front of a person.

        A model list is untrusted input: `lx models` puts text from a remote
        server in front of a person, and it was the first place in this project
        to do so — an error body, a wrong-shape reply, an embeddings reply and
        a `URLError` reason are the others, and `_refusal` and `_excerpt` hold
        those. Measured
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
        backend answered the wrong shape needs to see some of it, so `str(data)`
        goes through `_excerpt` — redacted of every value the request carried,
        tamed, and only then cut to 300 characters, in that order, because a cut
        taken first left the head of a key on screen. `repr` is not what makes
        it safe: a reply whose top level is a JSON *string* reaches the message
        unquoted, because `str` of a string is the string; the control
        characters and the bidirectional overrides are gone either way, which is
        the property that matters. Everything after it names a type and nothing
        else, so there is no second place for a backend's own text to arrive.
        """
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise self._refusal(
                f"{self.name}: {printable_url(url)} did not answer an embeddings list "
                f"(expected a `data` array, got {type(data).__name__}): "
                f"{self._excerpt(str(data), 300)}{self._url_hint(None, url)}")
        if len(rows) != count:
            raise self._refusal(
                f"{self.name}: asked {printable_url(url)} for {count} embedding(s) and "
                f"it answered {len(rows)}. Nothing in the reply says which input each "
                f"row belongs to, so none of it is used.")
        out = [None] * count
        for row in rows:
            if not isinstance(row, dict):
                raise self._refusal(
                    f"{self.name}: {printable_url(url)} answered a row that is not an "
                    f"object ({type(row).__name__}).")
            idx = row.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < count:
                raise self._refusal(
                    f"{self.name}: {printable_url(url)} answered a row whose `index` is "
                    f"not a position in this request.")
            if out[idx] is not None:
                raise self._refusal(
                    f"{self.name}: {printable_url(url)} answered `index` {idx} twice, so "
                    f"at least one input has no vector and one has two.")
            out[idx] = self._vector(row.get("embedding"), url)
        width = len(out[0])
        if any(len(v) != width for v in out):
            raise self._refusal(
                f"{self.name}: {printable_url(url)} answered vectors of different "
                f"widths in one reply, which cannot be compared with each other.")
        if self._embed_dims is None:
            self._embed_dims = width
        elif width != self._embed_dims:
            raise self._refusal(
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
            raise self._refusal(
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
            raise self._refusal(
                f"{self.name}: {printable_url(url)} answered a row whose `embedding` is "
                f"an array of arrays, which is what llama.cpp's own `/embeddings` "
                f"handler returns.{self._url_hint(None, url)}")
        if len(value) > _MAX_EMBED_DIMS:
            raise self._refusal(
                f"{self.name}: {printable_url(url)} answered a {len(value)}-dimension "
                f"vector, which no real embedding model serves.")
        for x in value:
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                raise self._refusal(
                    f"{self.name}: {printable_url(url)} answered a vector holding "
                    f"{type(x).__name__}, not numbers.")
        try:
            vec = array("f", value)
        except OverflowError:
            raise self._refusal(
                f"{self.name}: {printable_url(url)} answered a vector holding a whole "
                f"number too large to be a coordinate.") from None
        if not all(map(math.isfinite, vec)):
            raise self._refusal(
                f"{self.name}: {printable_url(url)} answered a vector holding a value "
                f"that is not a finite number this project can store. `json.loads` takes "
                f"the bare tokens NaN and Infinity, and a value merely too large for "
                f"single precision becomes one — none of them raise anywhere "
                f"downstream, they make every comparison false, so a poisoned reply "
                f"would report a clean store.")
        return vec

    # -- what a backend may say about the request it was sent ---------------
    #
    # A credential the request carried never reaches a reader through a
    # backend's own text. Four things hold that, and they are held here — in
    # the class that knows what was sent — rather than at the places a message
    # is printed. Every catcher (`cli.main`, `translate.run_batch` and
    # `retry_one`, `web/server._models`, the `/api/job` worker,
    # `audit.embed_texts`) formats `str(e)` and holds no secret; there are seven
    # of them today, and invariant 6's enumerated list of display surfaces has
    # been wrong five times, each a new path to an old value. So: `_refusal` is
    # the one constructor of `ProviderError` in any module that defines a
    # provider, and the whole of every message is scanned, wrapper text
    # included; `_excerpt` is the one place backend text is cut, and it cuts
    # after it redacts; `_request` refuses a 200 that quotes a credential before
    # a single reader parses it; and no exception `_request` raises carries a
    # backend's bytes on its chain, because nothing is raised inside a handler
    # there. `tests/test_provider.py` pins the first three by `ast` and the
    # fourth at runtime, so a site added later inherits them or fails the suite.

    def _credentials(self):
        """The values a message may not carry, deduplicated, in a fixed order.

        The API key, read from the environment as the header was built. Every
        `headers` value that is text, and the decimal of one that is a whole
        number, because `http.client.putheader` sends an `int` as its decimal —
        a hand-edited `headers.Authorization` *replaces* the computed one in
        `_request`'s `{**headers, **self.extra_headers}`, so a key can be sent
        while `api_key` is empty (measured). The userinfo of `base_url`, raw and
        unquoted, and where it has a password, that on its own — see
        `_userinfo`. And every value the transport added to a request of this
        provider's on its own, accumulated over its life — see
        `_note_transport`.

        **`base_url` userinfo leaves the machine through a proxy**, which is the
        measurement that put it here after the first version of this docstring
        said it never did. Without a proxy `http.client` fails on the netloc
        before a byte is sent, and that is the only case the claim had been
        measured on. Under `http_proxy` urllib writes the full URL as the
        request-target and `user:password@host` as the `Host` header; under
        `https_proxy` the `CONNECT` target carries it; and a proxy whose error
        page names either put the password on stderr beside a redacted key.

        Not in the set, each for a reason: a `base_url` query is refused before
        anything is sent; the fixed, non-secret headers this code sets
        (`Content-Type`, `anthropic-version`) stay readable, because an
        Anthropic 400 naming the version must; the model id and the provider
        name are not secrets; and the username part of a `user:password`
        userinfo on its own, which is not a secret either and, being a common
        word as often as not, would be removed from ordinary prose. A userinfo
        with no `:` is the whole of it and *is* in the set: `https://<token>@host`
        is the ordinary way a token travels in a URL.
        """
        values = [self.api_key]
        for value in self.extra_headers.values():
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, int) and not isinstance(value, bool):
                values.append(str(value))
        values.extend(self._userinfo())
        values.extend(self._transport_added)
        out = []
        for value in values:
            if value and value not in out:
                out.append(value)
        return out

    def _userinfo(self):
        """What `base_url` carries before its `@`, in the forms a proxy can echo.

        The raw userinfo — the netloc before its **last** `@`, since a password
        may hold one percent-encoded and a hand-edited file may hold one bare
        — whenever there is one; where it holds a `:`, the raw password after
        the **first** one as well; and `urllib.parse.unquote` of each, because
        a proxy may show either the bytes it received or what they decode to.
        A userinfo with no `:` is in the set whole: `https://<token>@host/v1`
        is the ordinary way a token travels in a URL, and the first version of
        this left it out as "a bare username", so a proxy quoting the request
        line printed the token in full beside a redacted key (measured). What
        stays out is the *username part* of a `user:password` userinfo on its
        own — see `_credentials`; the `_WHOLE` floor is what keeps a short
        common username in a bare `name@host` out of the set, and a longer one
        is removed from a backend's prose wherever it appears whole, which is
        the cost of not knowing a token from a name. Read off the spec and not
        off the request URL, because the spec's field is the thing the person
        has to go and fix, and `_request` refuses a URL `urlsplit` cannot read
        before this is ever asked.
        """
        base = self.spec.get("base_url")
        if not isinstance(base, str):
            return []
        try:
            netloc = urllib.parse.urlsplit(base.strip()).netloc
        except ValueError:
            return []
        if "@" not in netloc:
            return []
        userinfo = netloc.rpartition("@")[0]
        values = [userinfo, urllib.parse.unquote(userinfo)]
        if ":" in userinfo:
            password = userinfo.partition(":")[2]
            values += [password, urllib.parse.unquote(password)]
        return values

    def _base_url_query(self):
        """Whether `base_url` carries a query string — the write side's own predicate.

        `bool(urlsplit(...).query)`, which is what `cli._field_base_url` asks
        before it writes, so the two sides agree on `http://h/v1?` (no query:
        accepted by both) and on a `?` inside a fragment (not a query: accepted
        by both). A URL `urlsplit` cannot read answers `False`; the request then
        fails in `_request`'s masked branch. A method rather than a line in
        `_request`, because that function imports `urllib.error` and
        `urllib.request` and so cannot name `urllib.parse` before it does.
        """
        base = self.spec.get("base_url")
        if not isinstance(base, str):
            return False
        try:
            return bool(urllib.parse.urlsplit(base.strip()).query)
        except ValueError:
            return False

    def _gated(self):
        """The values a 200 reply may not quote `_GATE` characters of, deduplicated.

        The API key, the userinfo values and what the transport added — every
        credential **except a `headers` value**, and each only when the value
        itself is `_GATE` or longer. A `headers` value such as an
        `HTTP-Referer` URL can legitimately appear in a translation, and a
        refused reply is a lost translation where an over-redacted error costs
        a glance; the floor is the value's own length and never a spelling's,
        because an eighteen-character placeholder whose percent form is twenty
        was measured refusing a completion the raw form accepted.
        """
        values = [self.api_key] + self._userinfo() + list(self._transport_added)
        out = []
        for value in values:
            if len(value) >= _GATE and value not in out:
                out.append(value)
        return out

    @staticmethod
    def _spellings(values):
        """Every form a default encoder gives each value, deduplicated.

        A closed list, on purpose: a redaction that guessed at "anything that
        looks like a token" is not decidable (invariant 4), and this is. Per
        value: the value and the value stripped, because Python's `http.server`
        keeps a trailing space where other parsers strip it (measured, both);
        the two `json.dumps` escapings, each also with `/` as `\\/`, which PHP's
        `json_encode` emits by default — and each of those four also with `<`,
        `>`, `&`, `'` and `+` as `\\u003c`-style escapes, which Go's
        `encoding/json` writes for the first three and .NET's
        `System.Text.Json` for all five by default, and again with every
        `\\uXXXX` in upper-case hex, which .NET writes where Python writes
        lower; `repr`, which is how this project's own `str(data)` sites present
        a dict; `urllib.parse.quote` with its hex in either case; `html.escape`,
        also with `'` as `&#39;` (Go, Express) and `&#039;` (PHP) where Python
        writes `&#x27;`; for an ASCII value, the value with a NUL between every
        two characters and one at either end, which is what a UTF-16 body looks
        like after `decode("utf-8", "replace")` — `_tame` then turns each NUL
        into U+FFFD and every character of the key is legible between them
        (measured, both byte orders); and for a `Basic <base64>` value, the
        `user:password` it encodes and the password after the first `:`, because
        a proxy can echo what it decoded rather than what it was sent.

        Not spelled: case-folded, base64 of the value, NFKC/full-width, every
        ASCII character as `\\u`, numeric entities for ordinary characters. No
        default encoder looked at produces them, and a form nobody produces is
        a cost with no case behind it. That is a claim about the encoders
        looked at and not about encoders: the first version of this list
        stopped at `html.escape` and said the rest did not exist, and three of
        the forms above came from reading Go's, .NET's and PHP's defaults.

        Not decoded on the other side either. Unescaping the displayed body
        before matching — every `\\u201c` back to `“` — rewrites the non-secret
        text a person reads, and turns a literal `\\u001b` into a real ESC before
        `_tame` sees it. Measured, and refused.

        **The floor is a property of the value, not of a form.** A value
        shorter than `_WHOLE` gets no spelling at all: its JSON or HTML form can
        be `_WHOLE` or longer, and redacting that contradicts "a value shorter
        than `_WHOLE` is never redacted" — measured, a seven-character value
        redacted in three spellings. A form shorter than `_WHOLE` — a stripped
        value, a decoded `Basic` — is dropped as well, so the scan can take
        "every spelling is long enough" as given.

        `html` and `base64` are imported here rather than at module scope: about
        5 ms each at import against 42 for `import scriptorium.cli`, measured,
        and `_request` documents the same pattern for a heavier case.

        Nothing here raises. `quote` is given `surrogatepass` because an
        environment value can hold a lone surrogate — `os.environ` decodes with
        `surrogateescape` on POSIX — and this runs inside `_request`'s handlers,
        where a raise would chain the backend's bytes (see `_request`).
        """
        import base64
        import html

        out = []
        for value in values:
            if len(value) < _WHOLE:
                continue
            forms = [value, value.strip()]
            for dumped in (json.dumps(value)[1:-1],
                           json.dumps(value, ensure_ascii=False)[1:-1]):
                for json_form in (dumped, dumped.replace("/", "\\/")):
                    for form in (json_form, _JSON_EXTRA.sub(_json_escape, json_form)):
                        forms.append(form)
                        forms.append(_JSON_HEX.sub(_json_upper, form))
            forms.append(repr(value)[1:-1])
            quoted = urllib.parse.quote(value, safe="", errors="surrogatepass")
            forms.append(quoted)
            forms.append(_PERCENT.sub(_percent_lower, quoted))
            escaped = html.escape(value, quote=True)
            forms.append(escaped)
            forms.append(escaped.replace("&#x27;", "&#39;"))
            forms.append(escaped.replace("&#x27;", "&#039;"))
            if value.isascii():
                laced = "\x00".join(value)
                forms.append(laced + "\x00")
                forms.append("\x00" + laced)
            if value.startswith("Basic "):
                try:
                    decoded = base64.b64decode(value[len("Basic "):],
                                               validate=True).decode("utf-8")
                except ValueError:      # binascii.Error and UnicodeDecodeError both
                    decoded = None
                if decoded is not None:
                    forms.append(decoded)
                    if ":" in decoded:
                        forms.append(decoded.partition(":")[2])
            for form in forms:
                if len(form) >= _WHOLE and form not in out:
                    out.append(form)
        return out

    def _tables(self):
        """``(whole, grams, longest)`` for the current secret set, memoized on it.

        `whole` is the set of spellings, `grams` the set of their `_PIECE`-grams
        and `longest` the longest spelling's length — what `_marks` and
        `_excerpt` read. Building the gram set is the one cost here that grows
        with the value (a four-kilobyte header value has tens of thousands), so
        it is kept on the instance and rebuilt only when the spellings change,
        which they do when `_note_transport` records a value; the memo is one
        tuple replaced by assignment, so a batch thread reads the old one or
        the new one and never half of each.
        """
        spellings = tuple(self._spellings(self._credentials()))
        memo = self._scan_memo
        if memo is not None and memo[0] == spellings:
            return memo[1:]
        whole = frozenset(spellings)
        grams = frozenset(s[k:k + _PIECE] for s in spellings
                          for k in range(len(s) - _PIECE + 1))
        longest = max(map(len, spellings), default=0)
        self._scan_memo = (spellings, whole, grams, longest)
        return whole, grams, longest

    def _marks(self, text):
        """One byte per position of ``text``: 1 where a removable run covers it.

        A removable run is an occurrence of a whole spelling, or `_PIECE` or
        more consecutive characters of one. The wholes are found with
        `str.find`. The pieces are one set lookup per position, and that is
        exact rather than a prefilter — though not because the grams decide
        whether a run is a substring of one spelling: a run whose grams come
        from two different spellings is a substring of neither, so "every gram
        of it is some spelling's" does not say that, and an earlier version of
        this docstring claimed it did. What the code relies on is weaker and
        true: **the union of the removable pieces equals the union of the
        text's `_PIECE`-grams that some spelling has.** Every removable piece
        is covered by its own grams, each of which is inside that same
        spelling, so marking the grams marks all of it; and every such gram is
        itself `_PIECE` consecutive characters of a spelling, so marking it
        marks nothing the rule would not. The slices here compare and never
        cut, which is why the `ast` guard over slicing admits this function
        beside `_excerpt`.
        """
        whole, grams, _longest = self._tables()
        marked = bytearray(len(text))
        for s in whole:
            at = text.find(s)
            while at != -1:
                marked[at:at + len(s)] = b"\x01" * len(s)
                at = text.find(s, at + 1)
        if grams:
            ones = b"\x01" * _PIECE
            for i in range(len(text) - _PIECE + 1):
                if text[i:i + _PIECE] in grams:
                    marked[i:i + _PIECE] = ones
        return marked

    @staticmethod
    def _render(text, marked, upto, budget=None):
        """``text[:upto]`` with every marked stretch collapsed to one `_MARKER`.

        Two stretches that overlap or touch are one marker: that is what makes
        the marks a union rather than a scan, and it is also why a backend that
        repeats the value back to back gets one marker for the lot. A stretch
        that reaches ``upto`` ends the output on its marker — for `_excerpt`,
        which scans a bounded window, what lies beyond may continue it and is
        never shown — and with a ``budget`` the output stops once it holds that
        many characters, because a caller that is about to cut it reads no
        further. **`_redact` passes none**: it used to pass the input's length
        plus one marker, and a marker is longer than a short spelling, so a
        message holding several short whole spellings grew past that budget
        and lost everything after the last marker it could afford — five
        copies of a twelve-character key came back as four markers and nothing
        else, and through `_refusal` that dropped the tail of the backend's
        sentence and this project's own advice after it (measured). Only a
        caller that cuts anyway may bound this. The finds are over the byte
        array and the appends are slices, so the cost is in the stretches and
        not in the characters.
        """
        out = []
        produced = 0
        i = 0
        while i < upto and (budget is None or produced < budget):
            j = marked.find(1, i, upto)
            if j == -1:
                out.append(text[i:upto])
                break
            out.append(text[i:j])
            out.append(_MARKER)
            produced += j - i + len(_MARKER)
            k = marked.find(0, j, upto)
            i = upto if k == -1 else k
        return "".join(out)

    def _redact(self, text):
        """``text`` with every run of a credential's spelling replaced by `_MARKER`.

        **A union, not a scan.** Every character that belongs to any removable
        run is marked (`_marks`), and each maximal marked stretch — overlapping
        or touching — becomes one marker (`_render`). The left-to-right scan
        this replaced took the longest run at each position and continued after
        it, which was wrong in two measured shapes: a whole short spelling that
        is a proper prefix of a longer, non-removable run was never considered,
        and a run consumed at one position hid a longer removable run that began
        inside it — `you sent [credential redacted]KLMNOPQRST.` for a
        twenty-character key whose first ten characters another header value
        ended with. Nothing else is touched, so "invalid model name" and "rate
        limited" read exactly as the backend wrote them.

        What it does not do, by design: a backend that prints the key in groups
        of eleven with a separator between, or laces it with format characters,
        shows all of it — it already holds the key, and a rule that chased that
        would have to remove ordinary text. The marker text can be written by a
        backend too, and then reads as a redaction. Neither is a defect of the
        rule; both are its edge, stated.

        **The identity when there is nothing to redact**, and a test pins it:
        with `api_key_env: ""` the secret set is empty, `"" in text` is true at
        every position, and a `str.replace` written without the floor would put
        a marker between every two characters.
        """
        whole, _grams, _longest = self._tables()
        if not whole:
            return text
        return self._render(text, self._marks(text), len(text))

    def _window(self, cap):
        """How many characters `_excerpt` scans and may show for a ``cap``.

        The unbounded rule's output reaches ``cap`` after at most ``cap``
        unmarked characters and `cap // len(_MARKER) + 1` markers — a marker
        is never cut, so no more of them start before the cut — and each marker
        stands for a stretch that is one spelling long, or two where the body
        quotes a header value that contains the key beside the key itself. So
        the window is ``cap`` plus that many markers' worth of two spellings,
        and the excerpt is exact whenever every stretch it shows is that short;
        a body that repeats a value back to back for longer than the window is
        one marker and ends there. Measured on the build before this one:
        redacting the whole of a sixteen-megabyte wrong-shape reply before the
        cut cost 2.2 s on ordinary text and 69 s against a four-kilobyte header
        value, reachable from a dropdown.
        """
        _whole, _grams, longest = self._tables()
        return cap + (cap // len(_MARKER) + 1) * 2 * longest

    def _excerpt(self, text, cap):
        """At most ``cap`` characters of a backend's text, redacted and tamed first.

        The one place in these modules where backend text is cut, and an `ast`
        guard says so — "redact before the cut" then holds by construction
        rather than by every site remembering it. Measured on the build before
        the last one: a key beginning at decoded character 490 left its first
        ten characters on screen whatever the match rule was, because the slice
        came first. A marker is never cut: a cut that falls inside one moves to
        where the marker ends, so nobody reads `[credential re` and wonders
        what the rest was.

        **It scans a bounded window and tames only what it emits.** The window
        is `_window`; the marks are taken over `_PIECE - 1` characters past it,
        so a run that begins in the window's last characters and ends beyond it
        is seen — every mark inside the window is then final. Two properties,
        each pinned by a test: never less redaction than the unbounded rule,
        because a marked stretch that reaches the window's end is emitted as its
        marker and ends the excerpt, and what was not scanned is not shown; and
        exact wherever the unbounded rule's output reaches ``cap`` inside the
        window. `_tame` runs on the cut and not on the window, because it is
        one character in, one out, and taming a megabyte to show 300 characters
        of it was 1.5 s of the 2.2 the whole thing cost.
        """
        whole, _grams, _longest = self._tables()
        if not whole:
            return _tame(text[:cap])
        window = self._window(cap)
        marked = self._marks(text[:window + _PIECE - 1])
        shown = self._render(text, marked, min(len(text), window), cap + len(_MARKER))
        if len(shown) > cap:
            end = cap
            start = shown.rfind(_MARKER, 0, cap + len(_MARKER) - 1)
            if start != -1 and start + len(_MARKER) > cap:
                end = start + len(_MARKER)
            shown = shown[:end]
        return _tame(shown)

    def _refusal(self, message):
        """The `ProviderError` every refusal in a provider module is built by.

        The only constructor of the class in any module that defines a provider
        — callers write `raise self._refusal(...)` — and the whole message is
        scanned, wrapper text included. That is the structural floor: an
        interpolation somebody adds next year is redacted and tamed without
        anybody remembering. No message literal in these modules carries a
        `Cc`/`Cf`/`Zl`/`Zp` character, so taming the whole message changes
        nothing the project wrote. The cost, accepted and pinned by a test that
        documents it: a key literally equal to wrapper text — `HTTP 401` —
        redacts the wrapper.
        """
        return ProviderError(_tame(self._redact(message)))

    def _quotes_credential(self, text):
        """Whether ``text`` holds `_GATE` consecutive characters of a gated value's spelling.

        The rule is every `_GATE`-window of every spelling of every `_gated`
        value, and the first version of this matched whole spellings only — so a
        completion quoting the first twenty-four characters of the key was
        accepted, banked through `lx commit` into the tracked memory with
        `lx check` green, while the same bytes in a 401 body were redacted.

        The windows are not searched for one by one. Every window of twenty
        contains a ten-character piece of the spelling that starts at a multiple
        of ten, so those pieces are searched with `str.find` — C speed over a
        sixteen-megabyte embeddings reply — and each hit is confirmed against
        the windows that contain it, at the offsets the hit fixes. A spelling
        every window of which carries a NUL — the UTF-16 forms — is not searched
        when the text has none. A test fuzz-compares this against the plain
        loop. What it costs is measured in the closing report of the change that
        wrote it; a piece that is a common word in the text costs one
        confirmation per occurrence.
        """
        gated = self._gated()
        if not gated:
            return False
        half = _GATE // 2
        laced = "\x00" in text
        pieces = {}
        for s in self._spellings(gated):
            if len(s) < _GATE:
                continue
            if not laced and max(map(len, s.split("\x00"))) < _GATE:
                continue
            for m in range(0, len(s) - half + 1, half):
                pieces.setdefault(s[m:m + half], []).append((s, m))
        for piece, sites in pieces.items():
            at = text.find(piece)
            while at != -1:
                for s, m in sites:
                    for k in range(max(0, m - half + 1), min(m, len(s) - _GATE) + 1):
                        start = at - (m - k)
                        if start >= 0 and text[start:start + _GATE] == s[k:k + _GATE]:
                            return True
                at = text.find(piece, at + 1)
        return False

    def _note_transport(self, req, headers):
        """Record every header value the transport added to ``req`` on its own.

        `urllib` stores a header name through `str.capitalize`, so the pairs this
        code added — ``headers`` and `extra_headers` — are compared that way,
        **as name and value**: a hand-edited `headers.Proxy-Authorization` is
        overwritten by `ProxyHandler` unconditionally, so a comparison by name
        alone found the value actually sent in neither set, and a 407 quoting it
        printed thirty-five windows of it (measured). What is left today is
        exactly `Proxy-authorization`, built from `http_proxy`/`https_proxy`
        userinfo: a proxy answering 407 quoted it back into the `HTTPError`
        branch, and after `urlopen` fails `req.headers` still carries it.
        `req.headers` and not `header_items()`: the unredirected half holds
        `Host` and `Content-length`, which are not secrets, and a test asserts
        the host survives the message.

        Accumulated for the life of the provider — one run — as an immutable
        tuple replaced by assignment, never shrinking, and read by
        `_credentials` and `_gated` alone. Kept on the instance rather than
        passed along because five refusal sites that quote a reply have no
        request to read it off, and a wrong-shape 200 quoting the proxy's
        credential printed sixty-six windows of it through them (measured). It
        runs after every attempt, before any message that quotes the reply is
        built. Nothing it does can raise.
        """
        if req is None:
            return
        # Only text values on our side of the comparison. What the transport
        # adds is always text, so a value of ours that is not text can match
        # nothing; and a hand-edited `headers` value that is a list or a block
        # is unhashable, which made this set comprehension raise `TypeError`
        # while the docstring above said nothing here could — inside the
        # `finally`, with the reply's exception as its context (measured).
        # `http.client` refuses such a header before a byte is sent, so the
        # refusal the reader gets is that one, and this only had to stop
        # standing in front of it.
        ours = {(name.capitalize(), value)
                for name, value in {**headers, **self.extra_headers}.items()
                if isinstance(value, str)}
        with self._transport_lock:
            added = tuple(value for name, value in req.headers.items()
                          if isinstance(value, str) and (name, value) not in ours
                          and value not in self._transport_added)
            if added:
                self._transport_added = self._transport_added + added

    # -- transport ---------------------------------------------------------
    def _backoff(self, attempt, retry_after=None):
        """How long to wait before the next attempt.

        `Retry-After` wins when the server sends a number, because a hosted API
        returning 429 knows its own window and an exponential guess does not.
        Its HTTP-date spelling is deliberately not honoured: parsing a date to
        wait on it is more machinery than this case earns, and falling through
        to our own backoff is never wrong, only slower. A number that is not
        finite falls through the same way, for the reason in the body.

        Jitter because a batch runs several requests concurrently against one
        server. Without it every one of them fails together and then retries in
        the same instant, which is the burst that caused the failure arriving
        again on schedule.
        """
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = None  # an HTTP-date; use the backoff we control
            # `nan` and the infinities survive `float()` and none of them
            # survives `time.sleep`, which raises `ValueError` — inside the
            # handler that called this, until `_request` stopped sleeping
            # there. They fall through like the date does (measured: a 429
            # with `Retry-After: nan` ended `lx models` in a traceback).
            if seconds is not None and math.isfinite(seconds):
                return min(max(seconds, 0.0), _MAX_BACKOFF)
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
        data = self._request(url, headers, payload=payload, method="POST",
                             what="a completion")
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
        # **Only http(s) leaves this function on the first hop.** `urllib`'s
        # stock opener also speaks `file:`, `ftp:` and `data:`, so a hand-edited
        # `base_url` of `file:///…` made the one endpoint a browser gesture can
        # reach into a local-file read whose content comes back inside the
        # wrong-shape error message. No configuration this project writes can
        # produce one — `_field_base_url` refuses an empty netloc — so this
        # costs nothing and closes the hand-edited case. A plain prefix test
        # rather than `urlsplit`, because that parser raises on some of the
        # inputs this is here to refuse. What it does not close: a backend
        # answering a redirect, which urllib follows to any scheme it speaks
        # and with `Authorization` still attached — that is
        # `docs/contracts/workbench-http.md` divergence (33), open, and its own
        # package.
        if not str(url).lower().startswith(("http://", "https://")):
            raise self._refusal(
                f"{self.name}: base_url must be an http:// or https:// address. This one "
                f"names another scheme, and the value is not repeated here.")
        # **A `base_url` carrying a query string is refused here, beside the
        # scheme check and before anything is sent.** Every request this project
        # builds appends its own path after `base_url` — `/models`,
        # `/embeddings`, `/chat/completions`, `/v1/models`, `/v1/messages` — so
        # `http://h/v1?key=X` is requested as `/v1?key=X/models`: the path moves
        # into the query and no endpoint that routes by path can be reached
        # (measured on the request line). Its one possible effect was a 404 page
        # quoting the query back — `Cannot GET /v1?key=…/models`, on stderr.
        # `lx config set` has refused to write the shape since 2026-08-12; a
        # hand-edited file still carries it. Read off the spec rather than off
        # `url`, because the spec's field is the thing the person has to go and
        # fix. **The predicate is the write side's** — `urlsplit(...).query`,
        # which `cli._field_base_url` uses — and not `"?" in url`: that accepted
        # `http://h/v1?` at `lx config set` and refused it at every request, and
        # refused a `?` inside a fragment as "a query string" (measured). A
        # URL `urlsplit` cannot read is not refused here; the request then
        # fails in the masked branch below.
        if self._base_url_query():
            raise self._refusal(
                f"{self.name}: base_url carries a query string. Every request appends "
                f"its own path after base_url, so the query moves that path into the "
                f"query and the request cannot reach an endpoint. Remove it — a "
                f"credential belongs in the environment variable named by "
                f"`api_key_env` — and the value is not repeated here.")
        # **The transport is imported here, not at module scope.** Every `lx`
        # command executes this module — `cli.py` binds `ProviderError` at module
        # scope, and Python runs `providers/__init__.py`, which imports this file
        # to build `KINDS`, before it binds any submodule — while these four
        # names are used in this function and nowhere else, and only once a
        # request is being sent. At module scope `urllib.request` brought `ssl`,
        # `http.client`, `socket` and the `email` package with fourteen of its
        # submodules into `lx --help`, on 3.9 through 3.12. Measured 2026-09-11
        # on 3.12, median of seven warm runs: `import scriptorium.cli` took 69 ms
        # with them there and 42 ms with them here.
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
        # **Nothing is raised, and nothing sleeps, inside a handler below.** An
        # exception raised inside an `except` block gets the exception being
        # handled as its `__context__`, and inside the `HTTPError` handler that
        # is the `HTTPError`, whose `__str__` is `HTTP Error 401: <reason
        # phrase>` — the backend's bytes. `raise last from None` hides the
        # context from a formatted traceback and leaves it on `__context__`,
        # the last object holding the value; and it did nothing for the two
        # measured ways a handler raised something *else*: a 401 whose body
        # stalls made `e.read()` raise a timeout inside the handler, a 429 with
        # `Retry-After: nan` made `time.sleep(nan)` raise `ValueError` inside
        # it, and neither is a `ProviderError` nor in `cli.main`'s exit-2
        # tuple, so `lx models` answered a traceback carrying forty-three
        # windows of the key. So each handler only records — what to raise,
        # whether it is final, what the server said about waiting — and the
        # loop raises or sleeps after the `try`. Every `ProviderError` this
        # function raises has `__cause__` and `__context__` both `None`, and
        # a test asserts it for every refusal shape the suite drives.
        for attempt in range(retries + 1):
            # Every sleep below is guarded by this. The loop used to wait after
            # its final attempt and then leave and raise anyway, so `retries=0`
            # cost a second of pure latency per failed call — which the suite
            # paid on every run.
            final = attempt == retries
            # `None` before the `try`, so a failure inside `Request(...)` below
            # reaches the handlers with no stale request from the attempt
            # before it and no unbound name.
            req = None
            fatal = None
            retry_after = None
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
                # `_note_transport` in a `finally`, so it runs before any
                # handler below reads the reply: urllib adds its own headers to
                # `req` inside `urlopen`, before the connection, and a message
                # built from the body has to know them by then.
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        # Bounded for a listing, unbounded for a completion. One
                        # extra byte is read so that "exactly at the cap" and
                        # "over it" are distinguishable without a second call.
                        raw = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
                        status = getattr(resp, "status", 200)
                finally:
                    self._note_transport(req, headers)
                if max_bytes is not None and len(raw) > max_bytes:
                    # `what` rather than a hard-coded "a model list". The listing
                    # was this branch's only caller for a year and the sentence
                    # said so; the moment a second bounded read existed, that
                    # sentence became a wrong one the new caller inherited
                    # silently. Found by the security-tier pass over this change.
                    raise self._refusal(
                        f"{self.name}: {printable_url(url)} answered more than "
                        f"{max_bytes} bytes for {what or 'this request'}, which no real "
                        f"backend does. Nothing was parsed.")
                # A `UnicodeDecodeError` holds the whole body as `.object` and a
                # `JSONDecodeError` as `.doc`, so neither is kept past its
                # handler and neither is chained: what survives of each is its
                # `str`, which names a position and never quotes the body.
                problem = None
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as e:
                    problem = str(e)
                if problem is None:
                    # **The 200 gate.** A reply that quotes a credential is
                    # refused whole, before `json.loads`, and it is not retried.
                    # One check here covers all three doors — `_post`, `_get`,
                    # `_embed_post` — and every reader of a reply that would
                    # otherwise have to know: `translate.parse_reply`'s refusal,
                    # which quotes completion content and holds no secret;
                    # `misattributed`, which quotes reply-chosen ids; `_listing`
                    # rows in a dropdown; and a completion whose content is
                    # stored by `accept` → `store.save_targets` → `lx commit` →
                    # `.lx/tm.*.jsonl`, which is tracked in git (measured: a
                    # key written there with `lx check` green — and, before the
                    # gate matched pieces, twenty-four characters of one).
                    # Refused and never rewritten — rewriting content is code
                    # writing a translation, and `misattributed` and `_vectors`
                    # already refuse a reply whole when it cannot be trusted. No
                    # excerpt of the body, which is the point.
                    if self._quotes_credential(text):
                        raise self._refusal(
                            f"{self.name}: {printable_url(url)} answered {what or 'this request'} "
                            f"with text that quotes the credential this request carried, so "
                            f"none of it is used.")
                    try:
                        return json.loads(text)
                    except ValueError as e:
                        # A 200 that is not JSON. Outside the handlers below,
                        # `json.loads` raised straight through them and out of
                        # `cli.main`, which has no `ValueError` in its exit-2
                        # tuple: a traceback and exit 1. Every other caller was
                        # shielded by `translate.run_batch`'s blanket
                        # `except Exception`; `cli.do_models` is not, and the
                        # trigger is the very misconfiguration `_url_hint` was
                        # added for — a proxy or a web UI at the root answering
                        # HTML. OpenRouter's bare host answers 200 with 131 KB
                        # of it. Raised here rather than retried: a server that
                        # answered the wrong content type will answer it again.
                        problem = str(e)
                raise self._refusal(self._not_json(url, status, problem))
            except urllib.error.HTTPError as e:
                # **The body is the backend's own bytes: read bounded, redacted,
                # tamed and cut, in that order, and `_excerpt` holds the
                # order.** A `_tame(...[:500])` written here cut first, so a key
                # beginning at decoded character 490 left its first ten
                # characters on screen whatever the match rule was — measured
                # two builds ago. The bound is what makes the redaction window
                # larger than the display window, where `e.read()` had no bound
                # at all.
                #
                # `_tame` is here for the reason `_sane` states for a listing
                # row, applied where the enumeration missed: this used to be
                # the only interpolation on this path reaching a reader
                # unescaped. Measured 2026-09-01 by the security-tier pass over
                # `GET /api/models`: a 4xx body of `x\x1b[2Ky` erases the line
                # it is printed on, and a `U+202E` reverses the display of
                # everything after it — in a terminal for `lx models`, and in a
                # browser, where `textContent` stops markup and does nothing
                # about a bidirectional override.
                #
                # The read is guarded: a body whose bytes never arrive raises a
                # timeout out of `e.read`, and one cut short raises
                # `IncompleteRead`, and either raised from here carried the
                # `HTTPError` — reason phrase included — out of this function as
                # `__context__` (measured). The message says the body could not
                # be read, and shows nothing of it.
                code = e.code
                hdrs = e.headers
                retry_after = hdrs.get("Retry-After") if hdrs is not None else None
                try:
                    text = e.read(_ERROR_BODY_BYTES).decode("utf-8", "replace")
                    unread = ""
                except (OSError, http.client.HTTPException):
                    text, unread = "", "(the body could not be read)"
                last = self._refusal(
                    f"{self.name}: HTTP {code} — {self._excerpt(text, 500)}{unread}"
                    f"{self._url_hint(code, url)}")
                if code not in _RETRYABLE:
                    fatal = last
            except urllib.error.URLError as e:
                # `e.reason` can be a remote server's own bytes: on a refused
                # `CONNECT` through a proxy it is `Tunnel connection failed:
                # 407 <the proxy's reason phrase>`, and an ESC in that phrase
                # reached a terminal untamed until this went through
                # `_refusal` (measured). `str(...)` of it and nothing more is
                # kept: the exception itself is not chained, for the reason
                # above the loop.
                reason = str(e.reason)
                last = self._refusal(
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
                    f"{self.name}: cannot reach {printable_url(url)} — {reason}. "
                    f"For a local server, check that it is running and that "
                    f"base_url names the right host and port."
                    f"{self._url_hint(None, url)}")
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
                # exchange happened, so no retry can help and this is fatal
                # rather than `last`.
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
                # fourth surface nobody had counted. Only the class name is
                # kept; the exception, whose message carries the value, is
                # neither chained nor referenced once this handler ends.
                fatal = self._refusal(
                    f"{self.name}: {printable_url(url)} could not be requested "
                    f"({type(e).__name__}). Check `base_url` — and a credential belongs in "
                    f"the environment variable named by `api_key_env`, never in the URL.")
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
                last = self._refusal(
                    f"{self.name}: timed out after {timeout}s."
                    + (advice or " Local models on CPU are slow — raise `timeout` or "
                                 "lower `batch.size`."))
            # Outside every handler: nothing is being handled here, so what is
            # raised carries no context, and a sleep that raises — it cannot,
            # since `_backoff` refuses a non-finite `Retry-After` — would not
            # either.
            if fatal is not None:
                raise fatal
            if not final:
                time.sleep(self._backoff(attempt, retry_after))
        raise last or self._refusal(f"{self.name}: request failed")

    def _not_json(self, url, status, problem):
        """The sentence for a 200 that could not be read as JSON.

        ``problem`` is the `str` of a `UnicodeDecodeError` or a
        `json.JSONDecodeError` — the string and never the exception, because
        the first holds the whole body as `.object` and the second as `.doc`,
        and the string of either names a position and never quotes the body.
        That is what lets it stand in a message; the message still goes through
        `_refusal` like every other. One function for the two sites that raise
        it, so the two cannot drift apart.
        """
        return (f"{self.name}: {printable_url(url)} answered {status} but not JSON "
                f"({problem}). Check that base_url points at the API rather than at a "
                f"web page.{self._url_hint(404, url)}")
