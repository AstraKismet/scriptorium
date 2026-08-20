"""Provider interface.

A provider takes a system prompt and a user message and returns text. That is the
entire contract — batching, JSON repair, placeholder validation, and retry of
individual segments all live above this layer in :mod:`scriptorium.translate`,
so adding a backend never means reimplementing the pipeline.
"""

import json
import os
import random
import socket
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

#: A model listing's own budget, bounded below the completion one. See `_get`.
_LIST_TIMEOUT = 30.0
_LIST_RETRIES = 1


class Provider:
    kind = "base"

    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        self.model = spec.get("model", "")
        self.timeout = float(spec.get("timeout", 120))
        self.temperature = float(spec.get("temperature", 0.2))
        self.max_tokens = int(spec.get("max_tokens", 4096))
        self.retries = int(spec.get("retries", 3))
        self.extra_headers = spec.get("headers", {}) or {}

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
        `evil[2KTOTALLY-DIFFERENT-MODEL` renders as its second half alone,
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
                and not any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")
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
        return sorted(out, key=lambda m: m["id"])

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

    def _post(self, url, payload, headers):
        """A completion, with the caller's own `timeout` and `retries`."""
        # `method` is explicit rather than inferred from `payload is not None`.
        # It was inferred, and `_post(url, None, headers)` therefore issued a
        # silent GET — unreachable from the two callers that exist today and a
        # trap set for the third. A POST with a JSON `null` body is a strange
        # thing to want, but it is not a GET.
        return self._request(url, headers, payload=payload, method="POST")

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
                             retries=min(self.retries, _LIST_RETRIES))

    def _request(self, url, headers, payload=None, method="POST",
                 timeout=None, retries=None):
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
            req = urllib.request.Request(url, data=body, method=method)
            for k, v in {**headers, **self.extra_headers}.items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
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
                detail = e.read().decode("utf-8", "replace")[:500]
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
