"""Provider interface.

A provider takes a system prompt and a user message and returns text. That is the
entire contract — batching, JSON repair, placeholder validation, and retry of
individual segments all live above this layer in :mod:`scriptorium.translate`,
so adding a backend never means reimplementing the pipeline.
"""

import http.client
import json
import math
import os
import random
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request

from ..config import has_version_segment, printable_url
from .errors import ProviderError

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
        """
        if code is not None and code != 404:
            return ""
        if has_version_segment(url):
            return ""
        # It deliberately does **not** end by recommending `lx models`. That was
        # the first draft, and `lx models` is itself one of the two callers — so
        # the failure of that very command ended by advising the reader to run
        # it. Naming a remedy is `cmd_config_set`'s job, where the command being
        # named is not the one that just failed.
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
                             max_bytes=_MAX_LIST_BYTES)

    def _request(self, url, headers, payload=None, method="POST",
                 timeout=None, retries=None, max_bytes=None):
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
                    raise ProviderError(
                        f"{self.name}: {printable_url(url)} answered more than "
                        f"{max_bytes} bytes for a model list, which no real backend "
                        f"does. Nothing was parsed.")
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
                last = ProviderError(
                    f"{self.name}: timed out after {timeout}s."
                    + (" A model listing is bounded well below the completion "
                       "timeout on purpose; if the server is simply slow to "
                       "answer, it is not answering." if method == "GET" else
                       " Local models on CPU are slow — raise `timeout` or "
                       "lower `batch.size`."))
                if not final:
                    time.sleep(self._backoff(attempt))
        raise last or ProviderError(f"{self.name}: request failed")
