"""Provider tests against a mock OpenAI-compatible server.

This is the contract that matters for local deployment: the request must be
plain enough that llama.cpp, Ollama, LM Studio, and vLLM all accept it.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.providers import build  # noqa: E402
from scriptorium.providers.base import ProviderError  # noqa: E402
from scriptorium.translate import translate_segments  # noqa: E402

SEEN = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n))
        SEEN["payload"] = payload
        SEEN["auth"] = self.headers.get("Authorization")
        body = json.dumps({"choices": [{"message": {"content": '{"s1": "ok"}'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StallHandler(BaseHTTPRequestHandler):
    """Answers with headers and then goes quiet.

    A *read* timeout, which is a different exception from a connect timeout: the
    connect case never reaches a socket read and surfaces as `URLError`, which
    the provider always handled. This one stalls after the headers are on the
    wire, and that is the case Python 3.9 raises `socket.timeout` for.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "1024")   # promised, never sent
        self.end_headers()
        time.sleep(1.5)                              # longer than any client timeout here


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


class BusyHandler(BaseHTTPRequestHandler):
    """503 to everything, instantly.

    A retryable status from a server that answers at once is what isolates the
    sleep: connecting to a dead port does not, because a refused connection is
    instant on Linux but a one-second timeout on Windows, so the wait being
    measured would be the platform's, not ours.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"error": "loading model"}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def busy():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), BusyHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


RATE_LIMITED = {"calls": 0}


class RetryAfterHandler(BaseHTTPRequestHandler):
    """429 with `Retry-After: 0` once, then success.

    The `0` is the whole assertion. The exponential backoff for the first retry
    is a second or more, so the elapsed time of a call that succeeds on the
    second attempt is what distinguishes "read the header" from "ignored it".
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        RATE_LIMITED["calls"] += 1
        if RATE_LIMITED["calls"] == 1:
            body = b'{"error": "slow down"}'
            self.send_response(429)
            self.send_header("Retry-After", "0")
        else:
            body = json.dumps({"choices": [{"message": {"content": '{"s1": "ok"}'}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def rate_limited():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RetryAfterHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.fixture(scope="module")
def stalling():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StallHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _cfg(url, **extra):
    return {"providers": {"local": {"kind": "openai", "base_url": url,
                                    "model": "test-model", "api_key_env": "", **extra}}}


def test_local_server_needs_no_auth_header(server):
    out = build("local", _cfg(server)).complete("sys", "user")
    assert out == '{"s1": "ok"}'
    assert SEEN["auth"] is None


def test_request_stays_minimal_by_default(server):
    build("local", _cfg(server)).complete("sys", "user")
    payload = SEEN["payload"]
    assert payload["stream"] is False
    assert "response_format" not in payload          # local runtimes often reject it
    assert "tools" not in payload
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]


def test_json_mode_is_opt_in(server):
    build("local", _cfg(server, json_mode=True)).complete("sys", "user")
    assert SEEN["payload"]["response_format"] == {"type": "json_object"}


def test_api_key_read_from_environment(server, monkeypatch):
    monkeypatch.setenv("LX_TEST_KEY", "sk-test")
    cfg = _cfg(server)
    cfg["providers"]["local"]["api_key_env"] = "LX_TEST_KEY"
    build("local", cfg).complete("sys", "user")
    assert SEEN["auth"] == "Bearer sk-test"


def test_unknown_provider_names_are_explicit():
    with pytest.raises(ProviderError, match="unknown provider"):
        build("nope", _cfg("http://x/v1"))


def test_a_base_url_is_masked_everywhere_it_can_be_read():
    """Invariant 6: every display surface shares one answer about a `base_url`.

    Two surfaces were missing from that list until 2026-08-13, and both are
    inside a run rather than inside a report, which is why nobody looked there.
    `describe()` is the first line `lx translate` prints and the first entry of
    `POST /api/job`'s `log`; the transport failure below reaches the same job's
    `error`. Both interpolated the raw value, so a hand-edited
    `https://user:SECRET@host/v1` was masked by `lx providers` and printed in
    full by the run beside it. Found by the security-tier pass over the frozen
    workbench contract; `docs/contracts/workbench-http.md` now states that a
    `base_url` is in printable form wherever it appears on that surface.

    The host survives on purpose — masking it would take the answer to "where is
    my document going" with it, and the failure message's own advice ("check
    that base_url ends in /v1") would stop being followable.
    """
    dirty = "https://user:SECRET@example.invalid/v1?key=abc"
    line = build("local", _cfg(dirty)).describe()
    assert "SECRET" not in line and "key=abc" not in line
    assert "example.invalid" in line and "/v1" in line

    p = build("local", _cfg("http://user:SECRET@127.0.0.1:1/v1", retries=0, timeout=0.2))
    with pytest.raises(ProviderError) as caught:
        p.complete("s", "u")
    assert "SECRET" not in str(caught.value)
    assert "127.0.0.1:1" in str(caught.value)

def test_a_listing_that_answers_the_wrong_shape_masks_the_url(models_server):
    """The surface the adversarial pass found, and it has to be *reached* to be tested.

    Both `list_models` methods interpolated the raw `base_url` into this message.
    A hand-edited `http://user:SECRET@host/v1?key=abc` was masked by
    `lx providers` and printed in full by `lx models` beside it — the same defect
    closed for `describe()` on 2026-08-13, reintroduced by a new surface. It
    reaches stderr through `cli.main`'s exit-2 tuple.

    **The server must answer**, which is why this uses the live mock rather than
    a dead port. The first version of this test pointed at `127.0.0.1:1`, failed
    to connect, and asserted against the *`URLError`* message — which was already
    masked and had been for a year. It passed without ever executing the line it
    was written for.

    **The credential shape used here is the query string, not userinfo**, and
    that is a measured constraint rather than a preference:
    `urllib.request.urlopen` cannot reach a URL carrying userinfo at all — it
    fails `getaddrinfo` before a byte goes out — so the `?key=SECRET` proxy
    shape is the only one that reaches this branch. `printable_url` strips both,
    and the userinfo half is covered by the `describe()` assertion above.
    """
    port = models_server.rsplit(":", 1)[1].split("/")[0]
    MODELS["payload"] = {"object": "list"}          # no `data` array: the shape error
    os.environ["LX_TEST_KEY"] = "sk-not-a-real-key"
    try:
        for kind, url in (("openai", f"http://127.0.0.1:{port}/v1?key=SUPERSECRET"),
                          ("anthropic", f"http://127.0.0.1:{port}?key=SUPERSECRET")):
            spec = {"providers": {"p": {
                "kind": kind, "base_url": url, "model": "m", "retries": 0,
                "timeout": 5, "api_key_env": "LX_TEST_KEY" if kind == "anthropic" else ""}}}
            with pytest.raises(ProviderError, match="model list") as caught:
                build("p", spec).list_models()
            said = str(caught.value)
            assert "SUPERSECRET" not in said, f"{kind} listing leaked the query"
            assert "sk-not-a-real-key" not in said, f"{kind} listing leaked the key"
            assert "127.0.0.1" in said, "the host survives, or the message is unfollowable"
    finally:
        os.environ.pop("LX_TEST_KEY", None)


def test_unreachable_server_gives_actionable_message():
    # timeout=0.2 rather than 1: port 1 is refused instantly on Linux but times
    # out on Windows, so a one-second timeout bought this message-shape check a
    # second of waiting on the development platform and nothing else.
    p = build("local", _cfg("http://127.0.0.1:1/v1", retries=0, timeout=0.2))
    with pytest.raises(ProviderError, match="base_url"):
        p.complete("s", "u")


def test_the_final_attempt_does_not_sleep(busy):
    """`retries=0` means one attempt and no waiting.

    Every failure path used to sleep unconditionally, including on the last
    attempt, and then leave the loop and raise anyway — a second of latency for
    a retry that was never going to happen, paid by every caller with its
    retries exhausted.
    """
    p = build("local", _cfg(busy, retries=0))
    start = time.perf_counter()
    with pytest.raises(ProviderError, match="HTTP 503"):
        p.complete("s", "u")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"took {elapsed:.2f}s — a sleep after the final attempt is back"


def test_retry_after_is_honoured_rather_than_the_backoff(rate_limited):
    RATE_LIMITED["calls"] = 0
    p = build("local", _cfg(rate_limited, retries=2))
    start = time.perf_counter()
    out = p.complete("s", "u")
    elapsed = time.perf_counter() - start
    assert out == '{"s1": "ok"}'
    assert RATE_LIMITED["calls"] == 2, "the 429 should have been retried exactly once"
    assert elapsed < 0.5, (
        f"took {elapsed:.2f}s — the backoff for attempt 0 is 1s or more, so "
        "`Retry-After: 0` was ignored")


def test_backoff_is_capped_jittered_and_survives_a_date():
    p = build("local", _cfg("http://x/v1"))
    # Jitter: same attempt, different waits, never below the exponential floor.
    waits = {p._backoff(3) for _ in range(20)}
    assert len(waits) > 1
    assert all(8.0 <= w < 9.0 for w in waits)
    # The ceiling holds whichever side the number comes from.
    assert p._backoff(30) == 20.0
    assert p._backoff(0, "999") == 20.0
    assert p._backoff(0, "-5") == 0.0
    # An HTTP-date is not parsed; it falls through to the backoff we control.
    assert 1.0 <= p._backoff(0, "Wed, 21 Oct 2015 07:28:00 GMT") < 2.0


TRANSLATED = {"requests": [], "bodies": []}


class TranslatingHandler(BaseHTTPRequestHandler):
    """Reads the request the way a model would, and answers every id it was asked for.

    Every other handler here replies with one fixed string, which is enough to
    test transport. This one is the far end of a real `translate_segments` run,
    so what it records is what actually crossed the wire — the check the
    neighbour-context work needed and could not get from a stub object.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body_in = json.loads(self.rfile.read(n))
        user = body_in["messages"][1]["content"]
        items = json.loads(user[user.index("["):])
        TRANSLATED["bodies"].append(body_in)
        TRANSLATED["requests"].append(items)
        answer = json.dumps({i["id"]: "已翻譯。" + i["id"] for i in items},
                            ensure_ascii=False)
        body = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def translating():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), TranslatingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_neighbour_context_survives_an_actual_request(translating):
    """Neighbour fields are message content and nothing else — invariant 7 holds.

    The request body keeps exactly the fields it had; a local runtime rejects
    what it does not recognize, so a context feature that reached for a request
    field would have cost llama.cpp support.
    """
    TRANSLATED["requests"].clear()
    TRANSLATED["bodies"].clear()
    segments = [{"id": f"s000{i}", "kind": "para", "masked": f"Sentence number {i}."}
                for i in range(1, 5)]
    doc = {"lang": "zh-TW", "tone": "literary", "segments": segments}
    cfg = dict(_cfg(translating), glossary="", dnt="",
               batch={"size": 2, "concurrency": 1, "context": 1})

    results, failures = translate_segments(segments, doc, cfg, provider_name="local")
    assert failures == []
    assert set(results) == {s["id"] for s in segments}

    assert all(set(b) == {"model", "messages", "temperature", "max_tokens", "stream"}
               for b in TRANSLATED["bodies"])

    first, second = TRANSLATED["requests"]
    assert [i["id"] for i in first] == ["s0001", "s0002"]
    assert set(first[0]) == {"id", "kind", "text", "after_id"}     # first of the document
    assert first[0]["after_id"] == "s0002"                         # inside the batch
    # And the batch edges reference nothing across the boundary, which since
    # 2026-09-04 is the whole of what a neighbour outside the request gets: the
    # inlined form put an id-less paragraph in the item and the model answered
    # it under a real segment's id. `translate._attach` carries the measurement.
    assert set(first[1]) == {"id", "kind", "before_id", "text"}
    assert set(second[0]) == {"id", "kind", "text", "after_id"}
    assert set(second[1]) == {"id", "kind", "before_id", "text"}   # last of the document


def test_style_sheet_request_shape_is_message_content_and_nothing_else(
        translating, tmp_path):
    """Invariant 7 over the style sheet: both halves are text, neither is a field.

    The temptation a voice feature creates is a `system` array, a `metadata`
    object, or a per-character `response_format` — and a self-hosted runtime
    rejects an unknown field rather than ignoring it, so any of the three would
    cost llama.cpp support. The preamble rides in the system message, the
    matched blocks ride in the user message, and the body keeps exactly the five
    keys it had before this landed.
    """
    TRANSLATED["requests"].clear()
    TRANSLATED["bodies"].clear()
    sheet = tmp_path / "style.txt"
    sheet.write_text("The narration is close third person.\n\n"
                     "[Mara]\nShe says 您 to no one.\n", encoding="utf-8")
    segments = [{"id": "s0001", "kind": "para", "masked": "Mara came down the hill."},
                {"id": "s0002", "kind": "para", "masked": "The lamps were lit."}]
    doc = {"lang": "zh-TW", "tone": "literary", "segments": segments}
    cfg = dict(_cfg(translating), glossary="", dnt="", style=str(sheet),
               batch={"size": 2, "concurrency": 1, "context": 0})

    results, failures = translate_segments(segments, doc, cfg, provider_name="local")
    assert failures == []
    assert set(results) == {"s0001", "s0002"}

    body = TRANSLATED["bodies"][0]
    assert set(body) == {"model", "messages", "temperature", "max_tokens", "stream"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "The narration is close third person." in body["messages"][0]["content"]
    assert "She says 您 to no one." in body["messages"][1]["content"]
    # The per-character half is not duplicated into the system message, which is
    # what keeps that message identical for every request of the run.
    assert "She says 您 to no one." not in body["messages"][0]["content"]


def test_a_stalled_read_gives_an_actionable_message(stalling):
    """A read timeout must reach the user as advice, not as a bare OSError.

    This can only *fail* on Python 3.9, where `socket.timeout` is its own class
    and the handler used to name only the builtin `TimeoutError`. From 3.10 the
    two are one class and the test passes either way — it is kept as the
    regression guard for the version CI still runs, not as local proof.
    """
    p = build("local", _cfg(stalling, retries=0, timeout=0.3))
    with pytest.raises(ProviderError, match="timed out"):
        p.complete("s", "u")


# ── an empty completion ────────────────────────────────────────────────────

EMPTYISH = {"content": ""}


class EmptyContentHandler(BaseHTTPRequestHandler):
    """A 200 whose `message.content` is whatever the test staged.

    The path this isolates was measured on 2026-08-20 and is not hypothetical:
    `complete()` returned `''` with no error, and the failure surfaced two hops
    later as `no JSON object in reply: ''` out of `parse_reply` — a message that
    reads as a protocol fault and sends the reader to look at the prompt.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({"choices": [{"message": {"content": EMPTYISH["content"]}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def emptyish():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), EmptyContentHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.mark.parametrize("content", ["", "   ", "\n\n", "\t"])
def test_an_empty_completion_is_a_provider_error(emptyish, content):
    """Every spelling of "the model said nothing", not only `None`.

    Whitespace is in the list because `translate.accept` strips before it
    judges, so a run of spaces was already destined to be refused one layer
    later as "empty translation" — the two agreeing at the transport is what
    makes the reason the reader sees the true one.
    """
    EMPTYISH["content"] = content
    p = build("local", _cfg(emptyish, retries=0))
    with pytest.raises(ProviderError, match="empty completion"):
        p.complete("s", "u")
    EMPTYISH["content"] = ""


def test_a_completion_that_is_not_text_is_a_shape_error(emptyish):
    """A gateway answering the content-parts shape puts a list here.

    Before the empty-completion guard the list was returned unchanged and failed
    in `parse_reply`; with a bare `.strip()` it would fail here as an
    `AttributeError`, which is worse than either. It belongs with the other
    shape refusal.
    """
    EMPTYISH["content"] = [{"type": "text", "text": "hi"}]
    p = build("local", _cfg(emptyish, retries=0))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        p.complete("s", "u")
    EMPTYISH["content"] = ""


def test_a_completion_of_a_single_zero_is_not_empty(emptyish):
    """`"0"` is a reply, and the guard must not eat it.

    Cheap, and it pins that the decision is made on the stripped *string* rather
    than on the truthiness of whatever was parsed.
    """
    EMPTYISH["content"] = "0"
    assert build("local", _cfg(emptyish, retries=0)).complete("s", "u") == "0"
    EMPTYISH["content"] = ""


# ── asking a backend what it serves ────────────────────────────────────────

MODELS = {"payload": None, "method": None, "body_len": None, "auth": None}


class ModelsHandler(BaseHTTPRequestHandler):
    """`GET /v1/models`, answering whatever the test staged.

    It records the method and the body length, because *how* the listing is
    fetched is part of the contract rather than an implementation detail:
    llama.cpp answers `POST /v1/models` with a 404 while `POST /models` means
    "add a model to the router". A listing that sent a body could therefore
    reach a mutating endpoint on a real server. Measured against build
    `b9892-ee445f93d`, 2026-08-20.
    """

    def log_message(self, *a):
        pass

    def do_GET(self):
        MODELS["method"] = self.command
        MODELS["body_len"] = self.headers.get("Content-Length")
        MODELS["auth"] = self.headers.get("Authorization")
        body = json.dumps(MODELS["payload"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def models_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_a_plain_openai_model_list_is_read(models_server):
    MODELS["payload"] = {"object": "list",
                         "data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}
    rows = build("local", _cfg(models_server, retries=0)).list_models()
    assert rows == [{"id": "gpt-4o", "status": ""},
                    {"id": "gpt-4o-mini", "status": ""}], "sorted by id; status empty"


def test_a_listing_is_a_GET_carrying_no_body(models_server):
    MODELS["payload"] = {"data": [{"id": "m"}]}
    build("local", _cfg(models_server, retries=0)).list_models()
    assert MODELS["method"] == "GET"
    assert MODELS["body_len"] is None, "a listing must not carry a request body"


def test_a_listing_sends_no_auth_header_without_a_key(models_server):
    MODELS["payload"] = {"data": [{"id": "m"}]}
    build("local", _cfg(models_server, retries=0)).list_models()
    assert MODELS["auth"] is None, "a keyless local server wants no Authorization"


def test_a_routers_status_object_is_read_as_its_value(models_server):
    """llama.cpp's router puts an object in `status`, not a string.

    Measured against build `b9892-ee445f93d`: `status` is
    `{"value": "sleeping", "args": [...], "preset": "..."}`. Reading it as a
    string is the mistake this pins — and `args` is the whole `llama-server`
    argv, which carries absolute paths off the operator's disk and is not ours
    to print. The reader wants `value` and nothing else in there.
    """
    MODELS["payload"] = {"data": [
        {"id": "b/model:Q4", "status": {
            "value": "sleeping",
            "args": ["llama-server.exe", "--model", "C:/private/secret.gguf"],
            "preset": "[b/model:Q4]\nmodel = C:/private/secret.gguf\n"}},
        {"id": "a/model:Q8", "status": {"value": "loaded"}},
    ]}
    rows = build("local", _cfg(models_server, retries=0)).list_models()
    assert rows == [{"id": "a/model:Q8", "status": "loaded"},
                    {"id": "b/model:Q4", "status": "sleeping"}]
    assert not any("secret.gguf" in str(r) for r in rows), "argv and preset stay behind"


def test_a_model_id_carrying_a_dot_and_a_colon_survives(models_server):
    """A router's ids are not identifier-shaped, and nothing may split them.

    `unsloth/Qwen3.6-35B-A3B-GGUF:IQ2_M` carries a slash, a dot and a colon. The
    dot earns the test: dotted-key addressing is the one mechanism here that
    could plausibly read it as structure, and in this position it is a *value*.
    """
    MODELS["payload"] = {"data": [{"id": "unsloth/Qwen3.6-35B-A3B-GGUF:IQ2_M"}]}
    rows = build("local", _cfg(models_server, retries=0)).list_models()
    assert rows == [{"id": "unsloth/Qwen3.6-35B-A3B-GGUF:IQ2_M", "status": ""}]


@pytest.mark.parametrize("payload", [
    {"object": "list"},          # no `data` at all
    {"data": {"gpt-4o": {}}},    # an object where the array belongs
    ["gpt-4o"],                  # a bare array, without the envelope
])
def test_a_reply_that_is_not_a_model_list_is_refused(models_server, payload):
    """Named as a listing failure rather than flattened into an empty list.

    "This backend serves nothing" and "that endpoint answered something else"
    are different facts, and a UI that renders the first for the second shows an
    empty dropdown with nothing visibly wrong.
    """
    MODELS["payload"] = payload
    p = build("local", _cfg(models_server, retries=0))
    with pytest.raises(ProviderError, match="model list"):
        p.list_models()


def test_a_row_without_an_id_is_dropped_rather_than_fatal(models_server):
    MODELS["payload"] = {"data": [{"id": "keep"}, {"object": "model"}, {"id": ""}, "junk"]}
    assert build("local", _cfg(models_server, retries=0)).list_models() == [
        {"id": "keep", "status": ""}]


@pytest.mark.parametrize("evil", [
    "evil\x1b[2K\rTOTALLY-DIFFERENT-MODEL",   # erase-line + CR: renders as the second half alone
    "line1\nline2-forged-row",                # forges a whole extra row in the listing
    "bell\x07and\x08backspace",
    "tab\tseparated",
    "bidi‮override",                     # U+202E reverses everything after it
    "zero​width",
])
def test_a_model_id_carrying_a_control_character_is_dropped(models_server, evil):
    """`lx models` is the one place remote text reaches a terminal.

    Measured 2026-08-20 against this very payload: `\\x1b[2K\\r` erases the line
    it is on, so a backend could display one id while being another, or wipe out
    the advisory line printed under the listing; an embedded newline forged a row
    that looked like a model. `--json` was never exposed, because `json.dumps`
    escapes all of these — which is why the fix is at the boundary and not at the
    print, so both surfaces agree.

    Dropping the `_has_control` filter makes this fail. That mutation was run.
    """
    MODELS["payload"] = {"data": [{"id": "good"}, {"id": evil}]}
    assert build("local", _cfg(models_server, retries=0)).list_models() == [
        {"id": "good", "status": ""}]


def test_a_control_character_in_a_status_drops_the_row_too(models_server):
    """`status` prints beside the id and is remote text just the same."""
    MODELS["payload"] = {"data": [{"id": "good"}, {"id": "bad", "status": "load\red"}]}
    assert build("local", _cfg(models_server, retries=0)).list_models() == [
        {"id": "good", "status": ""}]


def test_an_ordinary_router_id_is_not_mistaken_for_control_characters(models_server):
    """The filter must not eat the ids this feature exists to print.

    Slashes, colons, dots, underscores and CJK are all category `L`, `N`, `P` or
    `S` — never `Cc` or `Cf`. Asserted because a filter written as "printable
    ASCII only" would pass every test above and quietly refuse a real id.
    """
    ids = ["mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M:IMMERSIVETRANSLATE",
           "unsloth/Qwen3.6-35B-A3B-GGUF:IQ2_M",
           "ScrambieBambie_Snowpiercer-15B-v2_Q8_0",
           "ggml-org/bge-m3-Q8_0-GGUF:Q8_0",
           "模型/繁體-中文:Q4"]
    MODELS["payload"] = {"data": [{"id": i} for i in ids]}
    rows = build("local", _cfg(models_server, retries=0)).list_models()
    assert [r["id"] for r in rows] == sorted(ids)


def test_an_anthropic_listing_refuses_without_a_key():
    """The refusal `complete` already gives, for the same reason: that endpoint is authenticated."""
    cfg = {"providers": {"c": {"kind": "anthropic", "base_url": "https://example.invalid",
                               "model": "m", "api_key_env": "", "retries": 0}}}
    with pytest.raises(ProviderError, match="no API key"):
        build("c", cfg).list_models()


# ── what the adversarial pass over this feature found ──────────────────────

class NotJsonHandler(BaseHTTPRequestHandler):
    """A 200 that is HTML, which is what a proxy or a web UI at the root answers."""

    def log_message(self, *a):
        pass

    def _reply(self):
        body = b"<html><body>404 not found</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply


@pytest.fixture(scope="module")
def not_json():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), NotJsonHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_200_that_is_not_json_is_a_provider_error(not_json):
    """Not a `JSONDecodeError` escaping to the top of the process.

    `json.loads` sat outside the three exception handlers, and `cli.main` has no
    `ValueError` in its exit-2 tuple — so `lx models` answered a traceback and
    exit 1. Every other caller was shielded by `translate.run_batch`'s blanket
    `except Exception`; `do_models` is not. The trigger is exactly the
    misconfiguration `_url_hint` exists for, and OpenRouter's bare host answers
    200 with 131 KB of HTML.
    """
    for call in (lambda p: p.complete("s", "u"), lambda p: p.list_models()):
        p = build("local", _cfg(not_json, retries=0))
        with pytest.raises(ProviderError, match="not JSON"):
            call(p)


def test_a_listing_is_bounded_below_the_completion_budget(models_server):
    """`timeout` and `retries` are sized for a model load; a listing never incurs one.

    The shipped `llamacpp` entry is `timeout: 600` with the default 3 retries,
    which is **40 minutes** before a black-holed listing gives up — measured — on
    a command `docs/windows-setup.md` sells as the quick way to check the server
    is up. Bounded downward only: a project that chose a shorter timeout keeps it.
    """
    from scriptorium.providers.base import _LIST_RETRIES, _LIST_TIMEOUT

    seen = {}
    p = build("local", _cfg(models_server, timeout=600, retries=3))
    real = p._request

    def spy(url, headers, payload=None, method="POST", timeout=None, retries=None,
            max_bytes=None):
        seen[method] = (timeout, retries)
        seen["max_bytes"] = max_bytes
        return real(url, headers, payload=payload, method=method,
                    timeout=timeout, retries=retries, max_bytes=max_bytes)

    p._request = spy
    MODELS["payload"] = {"data": [{"id": "m"}]}
    p.list_models()
    assert seen["GET"] == (_LIST_TIMEOUT, _LIST_RETRIES)
    assert _LIST_TIMEOUT < 600 and _LIST_RETRIES < 3

    p2 = build("local", _cfg(models_server, timeout=5, retries=0))
    p2._request = lambda *a, **k: seen.update({"short": (k.get("timeout"), k.get("retries"))}) or {"data": []}
    p2.list_models()
    assert seen["short"] == (5, 0), "a shorter budget than the cap is kept"


def test_post_stays_a_post_even_with_a_null_payload():
    """The verb is explicit, not inferred from whether there is a body.

    It was inferred, so `_post(url, None, headers)` issued a silent GET —
    unreachable from today's two callers and a trap for the third.
    """
    seen = {}
    p = build("local", _cfg("http://127.0.0.1:1/v1", retries=0, timeout=0.1))
    p._request = lambda url, headers, payload=None, method="POST", **k: seen.update(
        {"method": method, "payload": payload})
    p._post("http://127.0.0.1:1/v1/x", None, {})
    assert seen["method"] == "POST"


def test_an_anthropic_listing_drops_forged_rows_too():
    """The untrusted-reply rules belong to every backend, not to one file.

    The control-character filter was written private to `openai_compat.py`, and
    a `kind: "anthropic"` `base_url` is configurable — LiteLLM serves the
    Messages API — so this path could forge terminal rows while the other could
    not. Moving `_sane`/`_listing` onto `Provider` is what makes them agree.
    """
    from scriptorium.providers.base import Provider

    rows = [{"id": "keep"},
            {"id": "evil\x1b[2K\rTOTALLY-DIFFERENT"},
            {"id": "forged\nrow"},
            {"id": "bidi‮override"}]
    assert Provider._listing(rows) == [{"id": "keep", "status": ""}]


@pytest.mark.parametrize("evil", ["line separator", "para separator"])
def test_a_unicode_line_separator_is_dropped_as_well(models_server, evil):
    """`json.dumps(ensure_ascii=False)` escapes C0 and nothing else.

    An earlier comment here claimed `--json` was protected by that. It is not:
    C1, `Cf` and `Zl`/`Zp` all pass through it unescaped, and `U+2028` is a line
    break to a great many renderers. The **drop** is what protects both
    surfaces, which is why it is at the boundary and not at the print.
    """
    import json as _json
    assert evil in _json.dumps({"id": evil}, ensure_ascii=False), "the premise"
    MODELS["payload"] = {"data": [{"id": "good"}, {"id": evil}]}
    assert build("local", _cfg(models_server, retries=0)).list_models() == [
        {"id": "good", "status": ""}]


def test_an_overlong_field_is_dropped_before_it_can_pad_a_column(models_server):
    """`cmd_models` pads to the widest row it is handed, and the rows are untrusted.

    Measured: one 200k-character `status.value` among 2000 ordinary rows turned
    a listing into 400 MB of stdout in 0.76 s.
    """
    from scriptorium.providers.base import _MAX_FIELD

    MODELS["payload"] = {"data": [{"id": "good"},
                                  {"id": "x" * (_MAX_FIELD + 1)},
                                  {"id": "big", "status": "s" * 200_000}]}
    assert build("local", _cfg(models_server, retries=0)).list_models() == [
        {"id": "good", "status": ""}]


def test_do_models_asks_the_backend_that_routing_names(models_server):
    """`lx models` with no `--provider` asks whatever `routing.draft` points at.

    And it reports the model *this project would send*, resolved through
    `config.resolve_route` rather than read off the provider spec — which is the
    only reason to print the two together. A second resolver here is how the
    listing comes to mark a model the run would not have used.
    """
    from scriptorium import cli

    MODELS["payload"] = {"data": [{"id": "chosen"}, {"id": "other"}]}
    cfg = {**_cfg(models_server, retries=0), "routing": {"draft": "local"}}
    cfg["providers"]["elsewhere"] = {"kind": "openai", "base_url": "http://127.0.0.1:1/v1",
                                     "model": "never-reached", "api_key_env": "", "retries": 0}

    name, configured, rows = cli.do_models(cfg)
    assert name == "local"
    assert configured == "test-model", "the provider's own model, resolved not guessed"
    assert [r["id"] for r in rows] == ["chosen", "other"]

    # A routing entry naming a model of its own wins over the provider's, which
    # is `resolve_route`'s rule and must not be re-decided here.
    cfg["routing"] = {"draft": {"provider": "local", "model": "chosen"}}
    assert cli.do_models(cfg)[1] == "chosen"


# ── a base_url with no version segment ─────────────────────────────────────

class NotFoundHandler(BaseHTTPRequestHandler):
    """404 to everything, the way a server that does not serve this path answers.

    This is the shape the advice used to miss entirely. A `base_url` short of its
    version segment reaches a server that is running perfectly well and answers
    `404` — an `HTTPError` — while the "check base_url" sentence lived only on
    the `URLError` branch, which is *cannot connect*. The person who made the
    mistake was the one person the message never reached.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"error": {"message": "File Not Found", "code": 404}}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ForbiddenHandler(NotFoundHandler):
    """401, which means the route was found. The hint must stay silent."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"error": {"message": "no key", "code": 401}}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def missing_route():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), NotFoundHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"      # deliberately no /v1
    httpd.shutdown()


@pytest.fixture(scope="module")
def unauthorized():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ForbiddenHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"      # also no /v1
    httpd.shutdown()


def test_a_404_on_a_path_with_no_version_segment_says_so(missing_route):
    """The hint reaches the branch the mistake actually takes.

    Remove the `self._url_hint(e.code, url)` call from the `HTTPError` handler in
    `providers/base.py` and this fails — that is the mutation, and it was run.
    """
    p = build("local", _cfg(missing_route, retries=0))
    with pytest.raises(ProviderError, match="version segment"):
        p.complete("s", "u")


def test_the_hint_is_silent_when_the_path_already_has_a_version(missing_route):
    """`/v1` present and still a 404 means something else is wrong.

    Repeating the version advice there is the Continue.dev #7682 failure with the
    sign flipped: confident, irrelevant, and it sends the reader down the wrong
    path. Removing the `has_version_segment` early return in `_url_hint` makes
    this fail; removing the whole guard makes both this and the `/v2` case fail.
    """
    p = build("local", _cfg(missing_route + "/v1", retries=0))
    with pytest.raises(ProviderError) as caught:
        p.complete("s", "u")
    assert "HTTP 404" in str(caught.value)
    assert "version segment" not in str(caught.value)


@pytest.mark.parametrize("path", ["/v2", "/v1beta", "/api/v1", "/openai/v1", "/v3/"])
def test_no_ordinary_versioned_prefix_is_told_it_forgot_one(missing_route, path):
    """The shapes a `/v1`-shaped rule would have libelled.

    `/v2` is the measured one: Continue.dev told a user with that endpoint they
    had forgotten `/v1`. `/api/v1` and `/openai/v1` are how a proxy and Azure
    spell it, and `/v1beta` is Google's.
    """
    p = build("local", _cfg(missing_route + path, retries=0))
    with pytest.raises(ProviderError) as caught:
        p.complete("s", "u")
    assert "version segment" not in str(caught.value)


def test_a_401_is_never_told_to_check_its_path(unauthorized):
    """A 401 means the route was found; a path hint there is a plausible lie.

    Mutating `if code is not None and code != 404` to `if False` catches this.
    """
    p = build("local", _cfg(unauthorized, retries=0))
    with pytest.raises(ProviderError) as caught:
        p.complete("s", "u")
    assert "HTTP 401" in str(caught.value)
    assert "version segment" not in str(caught.value)


def test_an_unreachable_host_with_no_version_segment_gets_both_sentences():
    """The `URLError` branch keeps naming `base_url` and gains the same condition.

    Both halves matter: the field is worth naming however the URL is wrong, and
    the *prescriptive* half — "it must end in /v1" — was the part that was
    sometimes false.
    """
    p = build("local", _cfg("http://127.0.0.1:1", retries=0, timeout=0.2))
    with pytest.raises(ProviderError) as caught:
        p.complete("s", "u")
    assert "base_url" in str(caught.value)
    assert "version segment" in str(caught.value)


def test_lx_models_names_the_key_the_model_actually_came_from(models_server, capsys):
    """`configured` is resolved most-specific-first, so the remedy must follow it.

    With `routing.draft = {"provider": …, "model": …}` the value comes from the
    routing entry, not from `providers.<name>.model` — and both the note and the
    closing line said `providers.<name>.model` regardless. So the note quoted a
    value `lx config get providers.<name>.model` did not return, and following
    the printed remedy changed nothing at all. Found by the adversarial pass,
    2026-08-20.
    """
    import argparse as _argparse

    from scriptorium import cli

    MODELS["payload"] = {"data": [{"id": "listed"}]}
    args = _argparse.Namespace(provider=None, json=False)

    # Provider-supplied: the config key is the remedy.
    cfg = {**_cfg(models_server, retries=0), "routing": {"draft": "local"}}
    cli.cmd_models(args, cfg)
    out = capsys.readouterr().out
    assert "providers.local.model is 'test-model'" in out
    assert "lx config set providers.local.model" in out

    # Entry-supplied: the routing command is, and the config key is not named —
    # writing it would have left the note reading the same value.
    cfg["routing"] = {"draft": {"provider": "local", "model": "from-entry"}}
    cli.cmd_models(args, cfg)
    out = capsys.readouterr().out
    assert "routing.draft is 'from-entry'" in out
    assert "lx routing set draft local:" in out
    assert "lx config set providers.local.model" not in out


def test_do_models_takes_a_provider_override(models_server):
    """`--provider` names a different backend, and the model does not follow it.

    `resolve_route` drops the routing entry's model when the provider is
    overridden — a model id belongs to the backend that serves it — and this
    command inherits that rather than restating it.
    """
    from scriptorium import cli

    MODELS["payload"] = {"data": [{"id": "m"}]}
    cfg = {"providers": {
        "a": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "a-model",
              "api_key_env": "", "retries": 0},
        "b": {"kind": "openai", "base_url": models_server, "model": "b-model",
              "api_key_env": "", "retries": 0},
    }, "routing": {"draft": {"provider": "a", "model": "a-model"}}}

    name, configured, rows = cli.do_models(cfg, provider="b")
    assert (name, configured) == ("b", "b-model"), "a's model did not follow the override"
    assert rows == [{"id": "m", "status": ""}]


# ── what a backend may put in front of a person ────────────────────────────

class RudeHandler(BaseHTTPRequestHandler):
    """A 4xx whose *body* is hostile, which is a different surface from a row.

    `Provider._sane` filters a listing's `id` and `status`. It never saw an error
    body, so a backend that wanted to erase a terminal line or reverse the
    display of the advice printed under it only had to answer 400 and say so in
    prose. Measured 2026-09-01.
    """

    def log_message(self, *a):
        pass

    def _rude(self):
        body = RUDE["body"].encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _rude


RUDE = {"body": ""}


@pytest.fixture(scope="module")
def rude():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RudeHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.mark.parametrize("ch,what", [
    ("\x1b[2K", "an ANSI erase-line"),
    ("‮", "a bidirectional override"),
    (" ", "a line separator"),
    ("\n", "a newline"),
])
@pytest.mark.parametrize("call", ["list_models", "complete"])
def test_a_hostile_error_body_reaches_no_one_with_its_control_characters(
        rude, ch, what, call):
    """The same rule `_sane` states for a row, on the path that had no filter.

    Both entry points, because the body is `_request`'s and neither
    `list_models` nor `complete` owns it — a fix that covered only the listing
    would leave `lx translate` printing whatever a backend chose to send.
    """
    RUDE["body"] = f"refused: before{ch}after"
    p = build("local", _cfg(rude, retries=0))
    with pytest.raises(ProviderError) as e:
        getattr(p, call)() if call == "list_models" else p.complete("s", "u")
    msg = str(e.value)
    assert "refused: before" in msg, f"the body was dropped rather than tamed ({what})"
    assert ch not in msg, f"{what} survived into the message a person reads"
    assert "�" in msg


def test_a_listing_is_capped_so_a_row_count_cannot_grow_without_bound(models_server):
    """`_MAX_FIELD` bounds one field and says nothing about how many there are.

    The cut is after the sort, so which rows survive is a property of the ids
    rather than of the order the backend answered in.
    """
    from scriptorium.providers.base import _MAX_ROWS

    # **Answered in reverse**, which is the whole of the second assertion: with
    # an already-sorted payload a cut taken *before* the sort keeps exactly the
    # same rows, so the test passed either way and pinned nothing. Caught by a
    # mutation run on 2026-09-01.
    n = _MAX_ROWS + 50
    MODELS["payload"] = {"data": [{"id": f"m{i:06d}"} for i in reversed(range(n))]}
    rows = build("local", _cfg(models_server, retries=0)).list_models()
    assert len(rows) == _MAX_ROWS
    assert rows[0]["id"] == "m000000", "the cut was taken before the sort"
    assert rows[-1]["id"] == f"m{_MAX_ROWS - 1:06d}"


def test_a_base_url_carrying_userinfo_is_refused_without_printing_the_password():
    """`http.client.InvalidURL` is neither a `ValueError` nor an `OSError`.

    So it descended from none of the three masked branches in `_request`,
    `urllib` did not wrap it in a `URLError`, and the exception's own message
    quotes the netloc it choked on — which for a hand-edited
    `https://user:SECRET@host/v1` is `SECRET@host`. Measured 2026-09-01 by a
    probe over `GET /api/models`, which answered
    `400 {"error": "nonnumeric port: 'SECRET@host'"}`.

    `lx config set` refuses to write such a URL, so reaching this needs a
    hand-edited file — which is the case every other `printable_url` call site
    in this project exists for.
    """
    secret = "SUPERSECRETPASSWORD"
    cfg = {"providers": {"p": {"kind": "openai", "api_key_env": "", "retries": 0,
                               "base_url": f"https://user:{secret}@example.invalid/v1"}}}
    for call in (lambda p: p.list_models(), lambda p: p.complete("s", "u")):
        with pytest.raises(ProviderError) as e:
            call(build("p", cfg))
        assert secret not in str(e.value)
        assert "example.invalid" in str(e.value), "masked into uselessness"


# ── what the adversarial pass over `GET /api/models` found, 2026-09-01 ─────
#
# Every case below is a hand-edited `lx.config.json` — `lx config set` refuses
# each of them — which is the premise every `printable_url` call site in this
# project already exists for. What changed is the audience: a browser, reachable
# by opening a dropdown.

@pytest.mark.parametrize("base,pins", [
    ("//alice:{s}@example.invalid/v1", "the scheme guard"),
    ("http://alice:{s}@exa\nmple.invalid/v1", "the masked InvalidURL handler"),
    ("http://alice:{s}@exa\x00mple.invalid/v1", "the masked InvalidURL handler"),
    ("http://alice:{s}@example.invalid:notaport/v1", "printable_url's own guard"),
    ("http://alice:{s}@127.0.0.1:9/v1", "the masked URLError branch"),
])
def test_no_shape_of_userinfo_base_url_prints_the_password(base, pins):
    """Five shapes, five different guards, one rule.

    Written as a sweep rather than as one case because the enumeration is what
    this project keeps getting wrong: the measured leak was `InvalidURL` out of
    `urlopen`, the *first* guard written for it caught `ValueError` (which
    `InvalidURL` is not), and the sibling that `Request.__init__` raises needed a
    third guard again. Each row names the guard it actually exercises, because a
    row that passes for a neighbouring reason is a row that stops testing
    anything the day the neighbour moves — which is exactly what a mutation run
    caught here on 2026-09-01.

    **What is deliberately *not* pinned:** building the `Request` inside the
    `try` rather than above it. That was part of the same repair and it has no
    reachable case left, because the scheme guard refuses the only URL form
    `Request.__init__` rejects. It is kept as depth, and `base.py` says so.
    """
    secret = "SUPERSECRETPASSWORD"
    cfg = {"providers": {"p": {"kind": "openai", "api_key_env": "", "retries": 0,
                               "timeout": 1, "base_url": base.format(s=secret)}}}
    for call in (lambda p: p.list_models(), lambda p: p.complete("s", "u")):
        with pytest.raises(ProviderError) as e:
            call(build("p", cfg))
        assert secret not in str(e.value), pins


@pytest.mark.parametrize("scheme", ["file://", "ftp://", "gopher://", "data:text/plain,"])
def test_a_base_url_that_is_not_http_never_leaves_through_the_transport(scheme):
    """`urllib`'s stock opener also speaks `file:`, and one endpoint is now
    reachable by a browser gesture — so a `file:///` base_url turned a dropdown
    into a local-file read whose content came back in the wrong-shape message."""
    cfg = {"providers": {"p": {"kind": "openai", "api_key_env": "", "retries": 0,
                               "base_url": f"{scheme}/etc/passwd"}}}
    with pytest.raises(ProviderError) as e:
        build("p", cfg).list_models()
    assert "http:// or https://" in str(e.value)
    assert "passwd" not in str(e.value), "the refusal repeated the value"


@pytest.mark.parametrize("spec,why", [
    (5, "single value"),
    (["a"], "single value"),
    ({"kind": "openai", "timeout": "soon"}, "cannot be read"),
    ({"kind": "openai", "headers": "not-a-block"}, "cannot be read"),
    ({"kind": "openai", "retries": "many"}, "cannot be read"),
])
def test_a_malformed_provider_block_is_a_provider_error_not_a_traceback(spec, why):
    """`build` raised whatever Python raised on the way past.

    None of `AttributeError`, `TypeError` or a bare `ValueError` is in
    `cli.main`'s exit-2 tuple, so `lx models` answered a traceback; and
    `GET /api/models` answered `400` with `str(e)` — which for a numeric knob
    carries the configured value into a browser. `providers.available` beside
    this had reported every one of these shapes gracefully since 2026-08-20.
    """
    with pytest.raises(ProviderError) as e:
        build("p", {"providers": {"p": spec}})
    assert why in str(e.value)


def test_a_malformed_knob_refusal_never_repeats_the_value():
    """A mispasted key lands in whichever box the hand slipped into."""
    pasted = "sk-REDACTEDLOOKINGVALUE0123456789"
    with pytest.raises(ProviderError) as e:
        build("p", {"providers": {"p": {"kind": "openai", "timeout": pasted}}})
    assert pasted not in str(e.value)


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "nan"])
def test_a_non_finite_knob_is_refused_rather_than_serialized(value):
    """`float("Infinity")` succeeds and `json.dumps` writes the bare token
    `Infinity`, which is not JSON — so `JSON.parse` rejects the whole body and
    one hand-edited knob took `/api/state` down for the entire page."""
    from scriptorium.providers import available

    cfg = {"providers": {"p": {"kind": "openai", "base_url": "http://x/v1",
                               "api_key_env": "", "timeout": value}}}
    row = available(cfg)[0]
    assert row["timeout"] is None and "timeout" in row["error"]
    assert json.loads(json.dumps(available(cfg))), "the projection is not JSON"
    with pytest.raises(ProviderError):
        build("p", cfg)


def test_a_wrong_shape_reply_is_tamed_like_an_error_body(models_server):
    """`str(data)` is the identity on a `str`, so `repr` never escaped it.

    The comment that justified not taming here said `str(data)[:300]` "goes
    through `repr`". True when the decoded body is a dict or a list; false when
    a proxy or a web UI at the root answers a bare JSON string — which is
    exactly the misconfiguration this message was written for.
    """
    MODELS["payload"] = "‮hello[2Kthere"
    with pytest.raises(ProviderError) as e:
        build("local", _cfg(models_server, retries=0)).list_models()
    msg = str(e.value)
    assert "" not in msg and "‮" not in msg
    assert "�" in msg


class _FloodHandler(BaseHTTPRequestHandler):
    """Answers a model list far larger than any real backend serves."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        # Long ids rather than a huge count, so the body clears the cap without
        # the test spending its time on string building. ~215 bytes a row.
        pad = "x" * 190
        rows = ",".join(f'{{"id":"m{i:06d}{pad}"}}' for i in range(25_000))
        body = ('{"data":[' + rows + ']}').encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def flood():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FloodHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_a_listing_refuses_a_body_larger_than_any_real_backend_sends(flood):
    """`_MAX_ROWS` bounds the reply; this bounds the *read*.

    The two are different controls and only one existed: a hostile backend
    answering fifty megabytes of rows was measured driving one request thread to
    roughly 910 MB of peak memory — parsed in full, and only then trimmed to a
    thousand rows on the way out. Since `GET /api/models` exists that request is
    one dropdown change, on a threading server.

    The completion path keeps its unbounded read: a translation's body is
    legitimately large, and nothing reaches it by a browser gesture the way a
    listing now does.
    """
    from scriptorium.providers.base import _MAX_LIST_BYTES

    with pytest.raises(ProviderError) as e:
        build("local", _cfg(flood, retries=0)).list_models()
    assert "model list" in str(e.value) and "Nothing was parsed" in str(e.value)
    assert str(_MAX_LIST_BYTES) in str(e.value)


def test_an_ordinary_listing_is_nowhere_near_the_read_cap(models_server):
    """The cap must not be reachable by anything real — the guard against a
    guard that refuses working configurations."""
    from scriptorium.providers.base import _MAX_LIST_BYTES

    MODELS["payload"] = {"data": [{"id": f"m{i:03d}"} for i in range(200)]}
    rows = build("local", _cfg(models_server, retries=0)).list_models()
    assert len(rows) == 200
    assert len(json.dumps(MODELS["payload"])) * 20 < _MAX_LIST_BYTES


# ── what a run cost, read off the reply and never asked for ────────────────

#: What `UsageHandler` puts in the next reply's `usage` slot. A sentinel rather
#: than `None`, so a case can distinguish "send no usage key at all" — which is
#: what a backend that does not publish one does — from "send a null".
_NO_KEY = object()
USAGE = {"send": _NO_KEY}


class UsageHandler(BaseHTTPRequestHandler):
    """A completion whose `usage` object each test dictates.

    Against the real provider, not a stub: the counters live in
    `providers/base.py` and are fed from `_post`, so a duck-typed fake would be
    asserting the fake. Same three-line shape as every other scenario here.
    """

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        SEEN["payload"] = json.loads(self.rfile.read(n))
        reply = {"choices": [{"message": {"content": '{"s1": "ok"}'}}]}
        if USAGE["send"] is not _NO_KEY:
            reply["usage"] = USAGE["send"]
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def usage_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), UsageHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_reading_usage_adds_no_field_to_the_request(usage_server):
    """Invariant 7, on the change that most tempts a breach of it.

    The way to *ask* for usage on a streaming API is
    `stream_options: {"include_usage": true}` — a request field, and refused.
    This reads the reply, which costs the body nothing. The two pins earlier in
    this file assert the same set on the ordinary path; this one asserts it
    while usage is actually being collected, so a build that started asking for
    it fails here rather than in a test nobody connected to the feature.
    """
    USAGE["send"] = {"prompt_tokens": 11, "completion_tokens": 22}
    build("local", _cfg(usage_server)).complete("sys", "user")
    assert set(SEEN["payload"]) == {"model", "messages", "temperature",
                                    "max_tokens", "stream"}


def test_the_openai_shape_is_counted_and_the_total_is_computed(usage_server):
    """`total` is `prompt + completion` and is never read from the reply.

    The reply here claims a `total_tokens` of 9999 — which is what a gateway
    counting cached or reasoning tokens looks like — and it is ignored. One key
    cannot mean "what the backend said" on one backend and "what we added up"
    on another, or the number is not comparable with itself.
    """
    USAGE["send"] = {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 9999}
    p = build("local", _cfg(usage_server))
    p.complete("sys", "user")
    p.complete("sys", "user")
    assert p.usage.snapshot() == {"prompt": 22, "completion": 44, "total": 66,
                                  "replies": 2, "reported": 2}


def test_the_anthropic_shape_is_counted_by_the_same_reader(usage_server):
    """A subclass supplies two names and nothing else.

    Reading, validating, bounding and accumulating all live in `Provider`, which
    is the correction `providers/anthropic.py` already records: a rule about an
    untrusted reply written private to `openai_compat.py` left that backend
    unprotected for a day. So this drives the *Anthropic* field names through
    the shared reader, rather than through the Messages API's own transport,
    which is a different endpoint with a different body.
    """
    from scriptorium.providers.anthropic import AnthropicProvider

    assert AnthropicProvider.USAGE_FIELDS == ("input_tokens", "output_tokens")
    USAGE["send"] = {"input_tokens": 7, "output_tokens": 5}
    p = build("local", _cfg(usage_server))
    p.USAGE_FIELDS = AnthropicProvider.USAGE_FIELDS
    p.complete("sys", "user")
    assert p.usage.snapshot() == {"prompt": 7, "completion": 5, "total": 12,
                                  "replies": 1, "reported": 1}


@pytest.mark.parametrize("sent", [
    _NO_KEY,                                              # a backend that publishes none
    None,
    {},
    {"prompt_tokens": 11},                                # one half only
    {"prompt_tokens": 11, "completion_tokens": None},
    {"prompt_tokens": True, "completion_tokens": 22},     # isinstance(True, int)
    {"prompt_tokens": 11.0, "completion_tokens": 22.0},   # a float admits NaN
    {"prompt_tokens": float("nan"), "completion_tokens": 1},   # and here it is
    {"prompt_tokens": float("inf"), "completion_tokens": 1},
    {"prompt_tokens": "11", "completion_tokens": "22"},
    {"prompt_tokens": -11, "completion_tokens": 22},
    {"prompt_tokens": 10 ** 13, "completion_tokens": 22},
    {"prompt_tokens": {"n": 11}, "completion_tokens": 22},
    [11, 22],                                             # not an object at all
])
def test_a_reply_this_project_cannot_read_reports_nothing_rather_than_something(
        usage_server, sent):
    """The run completes, the reply counts, and the totals do not move.

    Partial credit is refused on purpose: a reply whose prompt count reads and
    whose completion count does not would move one axis while the run still
    called itself fully counted. `replies` moving while `reported` stays 0 is
    what lets a caller say "nobody told me" instead of "it was free".

    The float cases are not pedantry. Accepting floats means accepting `NaN` and
    `Infinity`, which `json.loads` produces from the bare tokens it takes as an
    extension and `json.dumps` writes back as those same bare tokens — invalid
    JSON, and precisely the shape `providers.base._finite` records taking
    `/api/state` down. From the wire it is a strictly worse source than a
    hand-edited config.
    """
    USAGE["send"] = sent
    p = build("local", _cfg(usage_server))
    assert p.complete("sys", "user") == '{"s1": "ok"}', "the run must still complete"
    assert p.usage.snapshot() == {"prompt": 0, "completion": 0, "total": 0,
                                  "replies": 1, "reported": 0}


def test_a_model_listing_is_not_counted_as_a_completion(models_server):
    """`_get` deliberately does not record. A listing has no cost to report, and
    counting one would put a `replies` on a run that never translated."""
    MODELS["payload"] = {"data": [{"id": "m1"}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
    p = build("local", _cfg(models_server, retries=0))
    p.list_models()
    assert p.usage.snapshot()["replies"] == 0


def test_two_providers_do_not_share_a_counter(usage_server):
    """Per instance, never per class: `lx run` reaches draft and repair through
    two providers, and a workbench can serve two jobs at once."""
    USAGE["send"] = {"prompt_tokens": 3, "completion_tokens": 4}
    first = build("local", _cfg(usage_server))
    second = build("local", _cfg(usage_server))
    first.complete("sys", "user")
    assert first.usage.snapshot()["total"] == 7
    assert second.usage.snapshot() == {"prompt": 0, "completion": 0, "total": 0,
                                       "replies": 0, "reported": 0}


def test_a_run_says_what_it_cost_through_the_one_sink_both_surfaces_read(usage_server):
    """The sentence is formatted once, in `translate.usage_line`, and reaches a
    terminal and the job log through the same `progress` callable.

    Driven through a real `translate_segments` run rather than by calling the
    formatter, because the thing that can break is the wiring: the totals live
    on a provider that function builds and nobody outside it can reach.
    """
    USAGE["send"] = {"prompt_tokens": 100, "completion_tokens": 50}
    lines, spent = [], {}
    segments = [{"id": "s1", "kind": "para", "masked": "One."}]
    doc = {"lang": "zh-TW", "tone": "literary", "segments": segments}
    cfg = dict(_cfg(usage_server), glossary="", dnt="",
               batch={"size": 25, "concurrency": 1, "context": 0})

    translate_segments(segments, doc, cfg, provider_name="local",
                       progress=lines.append, on_usage=spent.update)
    assert spent == {"prompt": 100, "completion": 50, "total": 150,
                     "replies": 1, "reported": 1}
    said = [line for line in lines if line.startswith("tokens:")]
    assert said == ["tokens: 100 in · 50 out · 150 total (1 of 1 reply reported usage)"]


def test_a_run_nobody_told_says_so_rather_than_implying_it_was_free(usage_server):
    """The degradation a backend publishing no `usage` object produces, worded
    so a reader cannot mistake it for a zero."""
    USAGE["send"] = _NO_KEY
    lines = []
    segments = [{"id": "s1", "kind": "para", "masked": "One."}]
    doc = {"lang": "zh-TW", "tone": "literary", "segments": segments}
    cfg = dict(_cfg(usage_server), glossary="", dnt="",
               batch={"size": 25, "concurrency": 1, "context": 0})

    translate_segments(segments, doc, cfg, provider_name="local", progress=lines.append)
    said = [line for line in lines if line.startswith("tokens:")]
    assert said == ["tokens: not reported — none of 1 reply carried a usage "
                    "object, so this run's cost is unknown"]
    assert "0" not in said[0], "a zero here would read as a free run"


def test_a_partial_count_is_a_floor_and_says_the_word():
    """A number that is not the whole cost must not be presentable as though it
    were — the first six words say so, so a reader does not have to notice a
    flag."""
    from scriptorium.translate import usage_line

    assert usage_line({"prompt": 9, "completion": 2, "total": 11,
                       "replies": 8, "reported": 5}) == (
        "tokens: at least 9 in · 2 out · 11 total — 5 of 8 replies reported "
        "usage, so this is a floor and not the run's cost")
    # Nothing at all when no reply arrived: a dry run, an empty selection, or a
    # run whose every request died before a body. "0 of 0" under a command that
    # called nothing is worse than silence.
    assert usage_line({"prompt": 0, "completion": 0, "total": 0,
                       "replies": 0, "reported": 0}) is None
    assert usage_line(None) is None
