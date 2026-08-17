"""Local review workbench.

The server is a thin HTTP shell over the same functions the CLI calls — there is
no second implementation of anything. Close the browser and the pipeline is
unchanged; the UI only exists because reading a hundred segments side by side is
faster with a mouse than with a JSON dump.

Binds to loopback by default. The workbench can spend money through configured
providers and reads files from the project directory, so exposing it on a
network interface is a deliberate act that prints a warning.
"""

import json
import mimetypes
import os
import posixpath
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..cli import (
    UnsafePath,
    UnusableTarget,
    confined_path,
    default_output,
    do_apply,
    do_check,
    do_extract,
    do_hold,
    do_render,
    do_select,
    do_translate,
    do_untracked,
    language_tag,
)
from ..config import ROUTING_STAGES, ConfigError, load_config, resolve_route
from ..docio import write_document
from ..providers import available
from ..store import append_tm, load_doc, load_tm, target_token, tm_records, tracked

STATIC = os.path.join(os.path.dirname(__file__), "static")

#: The version of `docs/contracts/workbench-http.md`, reported by `/api/state`.
#:
#: An integer rather than a semantic version string, and separate from
#: `__version__`, because it answers one question — "has anything a client reads
#: changed meaning" — and the package version answers a different one on every
#: release. It bumps on a removal or a meaning change and stays put for an
#: addition; the document states the rule and a test asserts the two agree.
#:
#: Not a response header. The set of headers this server sends is itself part of
#: the frozen surface, and `/api/state` is the endpoint a client must call first
#: in any case, since nothing else tells it which documents exist.
#:
#: 2 — M0 of the workbench rebuild, moved once and carrying five items together
#:     rather than five times: `candidates` renamed to `untracked`, the identity
#:     label normalized so one response stops carrying two spellings of it,
#:     `status` derived from the target text, an empty target refused, and the
#:     lost-update token. Moving it per item would have spent the property the
#:     freeze exists for, since a client is required to refuse a number it does
#:     not know. See `docs/decisions.md`, 2026-08-14.
CONTRACT_VERSION = 2

#: The three spellings of loopback. `serve()` binds one and the browser may be
#: pointed at any of them, so the bound literal alone is not the answer.
_LOOPBACK_BINDS = ("127.0.0.1", "::1", "localhost")
_LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")


def _stage_route(cfg, stage, provider=None, model=None):
    """One stage resolved to the backend and the model it will actually use.

    Resolved rather than echoed. A routing value is two shapes now — a provider
    name or `{provider, model}` — and a page that read one of them would silently
    break on the other: assigning the object to a `<select>`'s value yields
    `[object Object]`, the control shows nothing, and the run goes to whichever
    backend happened to be first in the list. Projecting one shape also keeps the
    workbench and `lx routing show` from disagreeing about which model is about
    to spend an hour, which is what `resolve_route` exists for.

    **A malformed entry is reported rather than raised**, and that is load-bearing
    on both callers rather than a convenience on one. `/api/state` draws the whole
    page, so one bad stage must not take the document list down with it; and
    `/api/translate`'s documented behaviour is that a routing problem fails
    *inside the job*, not on the request that starts it — resolving eagerly to
    report the answer back must not quietly convert that into a `400`.
    """
    try:
        name, chosen = resolve_route(cfg, stage, provider, model)
    except ConfigError as e:
        return {"provider": "", "model": "", "error": str(e)}
    return {"provider": name, "model": chosen}


def _routing_state(cfg):
    """Every stage resolved, in `_stage_route`'s shape."""
    return {stage: _stage_route(cfg, stage) for stage in ROUTING_STAGES}


def _own_hosts(port):
    """The authorities a loopback-bound workbench may legitimately be asked for.

    A browser omits the port when it is the scheme's default, so port 80 has to
    accept the bare name too — otherwise `lx web --port 80` would refuse its own
    page. Every other port is always spelled out, and the port is part of the
    comparison because another local process listening on another port is a
    different server.
    """
    hosts = {f"{name}:{port}" for name in _LOOPBACK_NAMES}
    if port == 80:
        hosts.update(_LOOPBACK_NAMES)
    return hosts


def _own_origins(bind_host, port):
    """Origins that can only be a page this server served, or `None` to degrade.

    `None` says the bind is not loopback, so no fixed set of names is trustworthy
    and the caller must fall back to the request's own `Host` — weaker, and
    `serve()` prints that when it binds.

    `http://` only: a page this server handed out is http, and `https://localhost`
    is a different origin belonging to someone else. All three spellings, because
    `serve()` opens `http://localhost:PORT` after binding `127.0.0.1`, so the
    default UI's `Origin` is the *name* and never the address — comparing against
    the bound literal alone would 403 the workbench's own buttons.
    """
    if bind_host not in _LOOPBACK_BINDS:
        return None
    return {f"http://{host}" for host in _own_hosts(port)}


def _require(path, **fields):
    """Fail an endpoint whose mandatory parameters are missing, the way it used to.

    These parameters were read as `q["src"]`, so a missing one raised KeyError
    and surfaced as a 400 quoting a bare key name. They are read through
    `dict.get` now, because a *missing* parameter must not be handed to
    `confined_path` and reported as an unsafe path — the caller sent no path at
    all. Same status, better sentence.
    """
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        raise ValueError(f"{path} needs {' and '.join(sorted(missing))}")


class _Handler(BaseHTTPRequestHandler):
    server_version = f"scriptorium/{__version__}"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # quieter than the default
        if self.path.startswith("/api/"):
            print(f"  {self.command} {self.path}", flush=True)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # -- request origin ---------------------------------------------------
    def _refuse_request(self):
        """`None` to accept, or the sentence to answer a 403 with.

        The call sites decide the scope, not a path test here: every POST, and
        GETs under `/api/` only. Static GETs are deliberately not gated — a
        top-level navigation carries `Sec-Fetch-Site: cross-site`, so gating
        them would refuse someone opening the workbench from a bookmark, and
        `index.html` ships inside the package and is not a secret.

        The bind is read from `self.server.server_address` and never from state
        `serve()` sets, because the tests construct the server directly and never
        call `serve()` — anything stashed there would be absent in exactly the
        place this control is measured.

        Three rules, in this order. **Host** is the only one that closes DNS
        rebinding and the only one that works on a GET: under a rebind the
        browser believes it is same-origin, so it sends no `Origin`, sends
        `Sec-Fetch-Site: same-origin`, and hands the response to the attacker's
        script — the other two rules see a request they like. **Sec-Fetch-Site**
        is a forbidden header name, so no page can set or delete it; `same-site`
        is refused alongside `cross-site`, because a page on another loopback
        port is same-site and is not us. **Origin** is compared by membership,
        port included.

        Absent is not the same as wrong, for all three. curl, an editor plugin
        and an older browser send no `Origin` and no `Sec-Fetch-Site`, and every
        browser sends a `Host`, so a request missing one is a local tool — the
        same trust bucket as `lx` itself, which can read and write these files
        directly and needs no HTTP to do it. What a hostile *page* cannot do is
        choose what any of these three say.
        """
        bind_host, port = self.server.server_address[0], self.server.server_address[1]
        allowed = _own_origins(bind_host, port)

        hosts = self.headers.get_all("Host") or []
        # The first only. It is the value the degraded branch compares an Origin
        # against, and there is no allowlist there to check it with.
        host = hosts[0].strip().lower() if hosts else ""
        if allowed is not None:
            if len(hosts) > 1:
                return (f"this request carried {len(hosts)} Host headers, so which server it "
                        f"meant is undecidable. Send one, or open http://127.0.0.1:{port}/ "
                        f"and work there.")
            if hosts and host not in _own_hosts(port):
                return (f"this request asked for host {host!r}. The workbench answers to "
                        f"localhost or 127.0.0.1 on port {port}, and a page reaching it "
                        f"under another name has had that name resolved to loopback — which "
                        f"is how DNS rebinding reads a local server. Open "
                        f"http://127.0.0.1:{port}/ instead; serving the workbench under a "
                        f"name of your own means binding a non-loopback address on purpose.")

        sites = self.headers.get_all("Sec-Fetch-Site") or []
        if len(sites) > 1:
            # The refusal Host and Origin already give a duplicate. No page can
            # send two — `Sec-` is a forbidden header prefix — so this closes a
            # consistency gap rather than a hole: reading the first of a
            # disagreeing pair, while the other two rules refuse the pair, was
            # an undocumented asymmetry in a three-rule gate.
            return (f"this request carried {len(sites)} Sec-Fetch-Site headers, and no "
                    f"reading of the pair is safe to act on. Send one, or open "
                    f"http://127.0.0.1:{port}/ and work there.")
        site = sites[0] if sites else None
        if site is not None and site.strip().lower() not in ("same-origin", "none"):
            return (f"this request came from another site (Sec-Fetch-Site: {site!r}). The "
                    f"workbench reads and writes files in the project directory and can "
                    f"spend money through configured providers, so it answers only the page "
                    f"it served itself. Open http://127.0.0.1:{port}/ and work there, or use "
                    f"`lx` from a terminal.")

        sent = self.headers.get_all("Origin") or []
        if len(sent) > 1:
            return (f"this request carried {len(sent)} Origin headers. At most one of them "
                    f"can be this workbench, and there is no reading of the pair that is "
                    f"safe to act on. Send one, or open http://127.0.0.1:{port}/ and work "
                    f"there.")
        if sent:
            # Membership, never falsiness and never a special case for "null":
            # "null" is a present three-byte value sent by a sandboxed iframe, a
            # `data:` URL, a `file://` page, a cross-origin redirect and — the
            # case met in the wild — an https page posting to this http server
            # under strict-origin-when-cross-origin. A falsiness test would let
            # all five through, and an empty Origin with them.
            origin = sent[0].strip().lower()
            if allowed is not None:
                ok = origin in allowed
            else:
                # Degraded, because the bind is not loopback: the only question
                # left is whether the page came from the name it is addressing.
                # This does NOT resist DNS rebinding — an attacker who controls
                # the name controls both sides of the comparison — so a
                # non-loopback bind keeps exactly the exposure it already had,
                # and `serve()` says so when it binds.
                ok = bool(host) and origin in (f"http://{host}", f"https://{host}")
            if not ok:
                return (f"Origin {origin!r} is not this workbench. It acts on local files "
                        f"and can spend money through configured providers, so it answers "
                        f"only the page it served itself. Open http://127.0.0.1:{port}/ and "
                        f"work there, or use `lx` from a terminal.")
        return None

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path.startswith("/api/"):
            # Only the API is gated on a GET — static paths stay open for the
            # bookmark-navigation reason `_refuse_request` documents. The gate
            # runs before load_config and before any path is looked at, so a
            # refused request does no filesystem work at all.
            refusal = self._refuse_request()
            if refusal:
                return self._send(403, {"error": refusal})
        try:
            if url.path.startswith("/api/"):
                return self._send(200, self._get(url.path, q))
            return self._static(url.path)
        except UnsafePath as e:
            # Before `except Exception`, and before any handler for ValueError:
            # UnsafePath subclasses ValueError, so a clause below would never
            # run. 403 says a control refused this, where 400 says the request
            # was malformed — and `_static` already answers 403 for this class.
            return self._send(403, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - surface to the UI
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        url = urlparse(self.path)
        # Unconditional, where do_GET gates only `/api/`: a POST has no
        # navigation case, so there is no legitimate cross-site POST to any
        # path on this server. Scoping this to a path prefix let `POST /api`
        # (no slash), `POST /apix/render` and `POST /` reach load_config() and
        # the confinement helper before dying at "unknown endpoint" — nothing
        # was reachable, but the gate must not depend on the router agreeing.
        refusal = self._refuse_request()
        if refusal:
            # Read the body before answering, bounded. A handler that replies
            # without consuming Content-Length leaves those bytes in the socket,
            # and on a keep-alive connection the next request is parsed out of
            # the leftover. `protocol_version` is HTTP/1.0 today so the
            # connection closes and this is invisible — the `/api/job` poll would
            # find it the day that line changes. Deliberately untested for the
            # same reason: while every response closes the connection, a drained
            # and an undrained socket are indistinguishable from outside, so a
            # test written today could only pin the spelling of these lines, and
            # a mutation run confirmed removing them leaves the suite green.
            # Whoever raises `protocol_version` to HTTP/1.1 owns writing the
            # real test — a refused POST followed by a second request on the
            # same connection.
            length = self.headers.get("Content-Length") or "0"
            self.rfile.read(min(int(length) if length.isdigit() else 0, 1 << 20))
            self.close_connection = True
            return self._send(403, {"error": refusal})
        try:
            return self._send(200, self._post(url.path, self._body()))
        except UnsafePath as e:  # before `except Exception`; see do_GET
            return self._send(403, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})

    def _static(self, path):
        """Serve a file from the static root, and nothing outside it.

        The guard this replaces decided on a string before the filesystem had a
        say: it normalized with `posixpath` and rejected a leading "..". Three
        things get past that, so there are now three answers.

        Percent-decoding happens first, because "%2e%2e%2f" walks up while
        looking inert. It also un-breaks any asset whose name holds a space or a
        non-ASCII character, which used to 404 for the same reason. It stays
        *here*, at the transport boundary, and is deliberately not moved into
        `confined_path`: a URL path arrives encoded and a JSON body does not, so
        a helper that decoded would turn the legal directory name `%2e%2e` in a
        request body into a traversal. The consistency fix would be the
        vulnerability.

        A backslash is then a separator on every platform. `posixpath` does not
        treat it as one and Windows' `open` does, so
        `x\\..\\..\\..\\..\\..\\pyproject.toml` passed the old guard untouched
        and then resolved five levels above the static root, into the repository.
        Rewriting it here rather than only where it is exploitable keeps the rule
        — and the test that pins it — identical on both platforms.

        Containment itself is `cli.confined_path`, rooted at the static directory
        rather than at the project: one answer to "is this path allowed", and one
        place to correct it when it is wrong. The refusal stays a plain-text
        `forbidden` rather than the helper's sentence, which names the *project*
        directory — not this root, and no use to a client fetching an asset.

        Loopback binding bounds the exposure and does not remove it: every local
        process could read anything this user could, no remote one could.
        """
        rel = posixpath.normpath(unquote(path).replace("\\", "/").lstrip("/"))
        if rel in ("", "."):
            # Before the helper, not after: it refuses the root directory itself,
            # and "/" means index.html here rather than a directory read.
            rel = "index.html"
        root = os.path.realpath(STATIC)
        try:
            confined_path(rel, "path", root=root)
        except UnsafePath:
            return self._send(403, b"forbidden", "text/plain")
        # Recomputed locally: the helper validates and hands back the caller's
        # string, deliberately not the resolved path.
        full = os.path.realpath(os.path.join(root, rel))
        if not os.path.isfile(full):
            # 404, not index.html with a 200. There is one page and no
            # client-side router, so an unknown path is a mistake — answering it
            # with a success made every typo render as a blank application, and
            # made the traversal above look like it had been served.
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            self._send(200, f.read(), f"{ctype}; charset=utf-8")

    # -- read -------------------------------------------------------------
    def _get(self, path, q):
        # Guarded by presence, not by endpoint name, so an endpoint that takes
        # `src` or `lang` cannot be added later without the check, and so
        # `/api/state` — which takes neither — behaves exactly as it did.
        # `is not None` is enough on a GET, where `_post` must test `in body`:
        # a query string cannot carry a JSON null, so absent and null cannot
        # diverge here. The asymmetry is a decision, not an oversight.
        src = q.get("src")
        if src is not None:
            src = confined_path(src, "src")
        lang = q.get("lang")
        if lang is not None:
            lang = language_tag(lang)
        cfg = load_config()
        if path == "/api/state":
            # Read once and handed on. `tracked()` loads every segment of every
            # document in the project, and the candidate scan used to make the
            # same call again to subtract what it found — two full reads to draw
            # one page, on the endpoint a client must call before it can do
            # anything at all.
            docs = tracked()
            untracked, collisions = do_untracked(cfg, docs)
            return {
                # First, and before `version`, because the two are read for
                # different reasons and are confused when they sit apart: this
                # one says whether the client still understands the reply, and
                # `version` says which build produced it.
                "contract_version": CONTRACT_VERSION,
                "version": __version__,
                "cwd": os.getcwd(),
                "targets": cfg.get("targets", []),
                "providers": available(cfg),
                "routing": _routing_state(cfg),
                # `d["source"]` is normalized by `store._meta` on the way out of
                # the database, so this and `untracked[].source` below are now one
                # spelling of one identity. They were two — `docs\guide.md` here
                # and `docs/guide.md` there — and a client was told not to compare
                # them. Contract divergence (13), closed on its remaining axis.
                "docs": [{
                    "source": d["source"], "lang": d["lang"],
                    "total": len(d["segments"]),
                    "done": sum(1 for s in d["segments"] if s.get("target")),
                } for d in docs],
                # `untracked`, spelling the command, the key and HANDOFF-203's
                # forthcoming field one way. The value is `lx untracked`'s, so the
                # two surfaces cannot answer this differently — which is what
                # closed the divergence: the glob-and-subtract lived only here.
                "untracked": untracked,
                # Which files one identity swallowed. Empty on any project whose
                # paths do not collide, which is most of them; never absent, so a
                # client does not have to tell "no collisions" from "an older
                # server". Contract divergence (18).
                "collisions": collisions,
            }
        if path == "/api/doc":
            # `q["src"]` used to raise KeyError here and 400. Both parameters are
            # still mandatory and the status is unchanged; only the sentence is
            # better than a bare key name.
            _require(path, src=src, lang=lang)
            doc = load_doc(src, lang)
            report, doc = do_check(src, lang, cfg, persist=False)
            issues = {}
            for i in report["issues"]:
                issues.setdefault(i["seg"], []).append(i)
            return {
                "source": doc["source"], "lang": doc["lang"], "tone": doc["tone"],
                "report": {k: report[k] for k in ("segments", "translated", "errors", "warnings", "by_rule")},
                "segments": [{
                    "id": s["id"], "kind": s["kind"], "status": s["status"],
                    "origin": s.get("origin"), "source": s["masked"],
                    # Always present, `null` when the segment is not held, so a
                    # client does not have to tell "not held" from "an older
                    # server" — the rule `collisions` follows on `/api/state`.
                    "review": s.get("review"),
                    "target": s.get("target") or "",
                    # What a client hands back to `POST /api/save` to prove its
                    # edit was based on this text. Derived, so it costs no column
                    # and no state version; see `store.target_token`.
                    "token": target_token(s.get("target")),
                    "issues": issues.get(s["id"], []),
                } for s in doc["segments"]],
            }
        if path == "/api/preview":
            _require(path, src=src, lang=lang)
            text, missing = do_render(src, lang, cfg, fallback=True)
            return {"text": text, "missing": missing,
                    "default_out": default_output(src, lang, cfg)}
        raise ValueError(f"unknown endpoint {path}")

    # -- write ------------------------------------------------------------
    def _post(self, path, body):
        # As in `_get`: by presence, so `/api/job` keeps working and a future
        # endpoint carrying a path cannot skip the check by being new.
        # Presence is `in body`, never `.get(...) is not None`: dict.get cannot
        # tell {"lang": null} from an absent key, and a JSON null used to skip
        # the check entirely — measured, {"lang": null} on /api/extract
        # answered 200 and created `.lx/docs/guide.md.None.json`. A present
        # null now reaches the validator and is refused as the non-string it is.
        src = body.get("src")
        if "src" in body:
            src = confined_path(src, "src")
        lang = body.get("lang")
        if "lang" in body:
            lang = language_tag(lang)
        cfg = load_config()
        if path == "/api/extract":
            doc, reused, rejected, _dropped = do_extract(src, lang, cfg, body.get("tone"),
                                               body.get("reset", False))
            return {"segments": len(doc["segments"]), "reused": reused, "rejected": rejected}
        if path == "/api/save":
            # `base` is optional and per id, so a client that has not opted in
            # writes exactly as it did. An empty target raises `EmptyTarget`,
            # which reaches the 400 below like every other refusal — the rule
            # lives in `do_apply` so `lx apply` cannot walk around it.
            # The fifth element is `do_apply`'s origin-precedence refusals, and
            # it is deliberately not projected: this endpoint hardcodes
            # `origin="human"`, so it is always empty. An endpoint that ever
            # passes another origin has to add a key here, and that is a version
            # decision rather than a line of plumbing.
            applied, unknown, stored, conflicts, _refused = do_apply(
                src, lang, cfg, body["targets"], origin="human", base=body.get("base"))
            return {"applied": applied, "unknown": unknown,
                    "stored": stored, "conflicts": conflicts}
        if path == "/api/hold":
            # Shapes are checked in `do_hold`, not here — a `bool()` at the
            # endpoint would turn `held: null` into a *release* and `held:
            # "false"` into a hold, which is the silent-coercion defect
            # `do_apply` refuses a mis-shaped `base` to avoid.
            applied, unknown = do_hold(src, lang, cfg, body.get("ids"),
                                       held=body.get("held", True))
            return {"applied": applied, "unknown": unknown}
        if path == "/api/check":
            report, _ = do_check(src, lang, cfg)
            return report
        if path == "/api/translate":
            return _translate_job(src, lang, cfg, body)
        if path == "/api/job":
            return _job_status(body["id"])
        if path == "/api/render":
            # Confined before the render, so a refused request does no work.
            # Truthiness on purpose, not presence: an empty `out` always meant
            # "use the default" — the old spelling was `body.get("out") or
            # default_output(...)` — and this package confines, it does not
            # tighten unrelated semantics. Every non-empty value is confined.
            out = body.get("out")
            if out:
                out = confined_path(out, "out")
            text, missing = do_render(src, lang, cfg, fallback=body.get("fallback", False))
            # `default_output` is not confined: it comes from `output_pattern` in
            # the project's own config, which is the same trust as a CLI argument
            # and may legitimately point at a sibling directory. A cross-site
            # POST cannot change it.
            out = out or default_output(src, lang, cfg)
            write_document(out, text)
            return {"wrote": out, "missing": missing}
        if path == "/api/commit":
            doc = load_doc(src, lang)
            return {"committed": append_tm(lang, tm_records(doc, load_tm(lang)))}
        raise ValueError(f"unknown endpoint {path}")


# ── background translation jobs ────────────────────────────────────────────

#: Every job this process still has a record of, in mint order — which is age
#: order, because ids only rise. Guarded by `_JOB_LOCK` together with the two
#: names below it; nothing here may be read or written outside that lock.
_JOBS = {}
_JOB_LOCK = threading.Lock()

#: The high-water mark: how many jobs this process has ever minted. A counter
#: rather than `len(_JOBS)`, and the difference is the whole of divergences (9)
#: and half of (4). A length goes *backwards* the moment anything is evicted, so
#: retention and id-uniqueness were the same defect: two runs would be handed one
#: id and a client polling the id it was given would watch someone else's run.
#: A counter only rises, so an id is never reused — and the numbers at or below
#: it are exactly the ones that have existed, which is what lets `/api/job`
#: answer "this finished and was dropped" differently from "this never was".
_JOB_SEQ = 0

#: The ids of finished jobs in **completion** order, and how many are kept.
#: Nothing is persisted — a mid-run crash is already cheap, because batches are
#: durable as they land, so a restart loses the progress log rather than the
#: work. `docs/decisions.md`, 2026-08-14, names the three triggers that would
#: force real records, and none of them exists.
#:
#: Two properties, and the second is why this list exists rather than a scan of
#: `_JOBS`. **A job that is not done is never evicted**, because its record is
#: the only way its client can find out what happened to it — and a job absent
#: from this list cannot be chosen. And "oldest" means oldest *to finish*, not
#: oldest to start: under mint order, an hour-long run minted first would be the
#: eviction candidate the instant it completed, so its client would poll once,
#: be told the record was dropped, and never learn the outcome. Completion order
#: puts the run that just ended at the back, where it is safest.
_JOB_DONE = []
_JOB_KEEP = 50

_JOB_ID_RE = re.compile(r"job(\d+)\Z")


def _mint_job(total):
    """A new job's state, minted **and** inserted under one lock acquisition.

    One acquisition and not two, which is what divergence (9) is about: the id
    used to be `f"job{len(_JOBS) + 1}"`, computed before the lock was taken, so
    two simultaneous requests could read the same length and the second would
    overwrite the first's state.
    """
    global _JOB_SEQ
    with _JOB_LOCK:
        _JOB_SEQ += 1
        job_id = f"job{_JOB_SEQ}"
        _JOBS[job_id] = state = {
            "id": job_id, "done": False, "log": [], "applied": 0,
            "failures": [], "refused": [], "error": None, "total": total}
        return state


def _finish_job(state):
    """Mark a run over and bring the table back inside its bound.

    Both under one lock acquisition, and this is the only place `done` is set —
    so the bound holds continuously rather than only at the moment the next run
    starts, and there is one place to read to know what retention does.
    """
    with _JOB_LOCK:
        state["done"] = True
        _JOB_DONE.append(state["id"])
        while len(_JOB_DONE) > _JOB_KEEP:
            _JOBS.pop(_JOB_DONE.pop(0), None)


def _job_status(job_id):
    """A job's record, or one sentence saying which kind of nothing this is.

    Still a `200` carrying `error` alone — divergence (5) is not closed here,
    only made informative. The distinction is a real question rather than a
    nicety: "your run is over and the log is gone" and "you asked for something
    that was never started" send a client to two different places, and before the
    high-water mark this server could not tell them apart.
    """
    with _JOB_LOCK:
        state = _JOBS.get(job_id)
        if state:
            return dict(state)
        # Compared against the spelling the minter produces, not against the
        # pattern: `job01` and `job0000001` match `job(\d+)` and were never
        # minted, so a client asking for one was told a run of theirs had
        # finished. The distinction is the entire purpose of the mark.
        found = _JOB_ID_RE.match(str(job_id or ""))
        if (found and str(job_id) == f"job{int(found.group(1))}"
                and 0 < int(found.group(1)) <= _JOB_SEQ):
            return {"error": f"job {job_id} has finished and its record has been "
                             f"dropped — only the most recent {_JOB_KEEP} are kept, "
                             f"and no job survives a restart. Re-read the document."}
        return {"error": "no such job"}


def _translate_job(src, lang, cfg, body):
    """Translation runs off-thread so the UI stays responsive on slow local models.

    What is *left* here is the job table and nothing else. Selecting the segments
    and running the model are `cli.do_select` and `cli.do_translate`, which is the
    whole of contract divergence (2): this function used to carry its own copy of
    the selection chain, and the copy disagreed with the CLI's about what
    `repair` means. `/api/job` stays a documented CLI gap — a browser cannot
    block for the minutes-to-hours a run takes — but the gap is the polling, not
    the pipeline.
    """
    doc = load_doc(src, lang)
    mode = body.get("mode", "draft")
    # Validated, not coerced. `bool("false")` is `True`, and this is the
    # opt-out for a rule whose failure direction is destructive and silent —
    # a form or a `URLSearchParams` body sends the string. `do_apply` sets
    # the precedent for a mis-shaped field on this surface: refuse it.
    over_human = body.get("overwrite_human", False)
    if not isinstance(over_human, bool):
        raise UnusableTarget(
            f"`overwrite_human` is true or false, and this request sent "
            f"{type(over_human).__name__}. It turns off the rule that keeps a "
            f"model run from replacing a person's wording, so it is not "
            f"guessed at: the string \"false\" would switch the guard off.")
    # Selection is given the same flag as the write, or the run pays a model
    # for every segment the write will refuse. See `cli._model_writable`.
    segments = do_select(doc, cfg, mode, ids=body.get("ids"), over_human=over_human)
    # Resolved once, here, and reported back: the only other place the answer
    # appears is a `log` line the contract forbids parsing, so a reviewer had no
    # way to tell which model produced the wording in front of them. One call to
    # the same `config.resolve_route` every other surface uses — a second site
    # resolving this independently is how the workbench and the CLI come to
    # describe different runs.
    route = _stage_route(cfg, mode, body.get("provider"), body.get("model"))

    state = _mint_job(len(segments))

    def log(msg):
        with _JOB_LOCK:
            state["log"].append(msg)

    def counted(written, refused):
        """`applied` moves while the run is going, and is right when it dies.

        The batch is already durable when this is called — `do_translate` banks
        it and hands the count on — so this only has to publish the number.
        Before version 2 it counted what a final apply touched and therefore
        stayed 0 for a run that raised after changing the document.

        `_JOB_LOCK` covers the counter and not the write, and not for the reason
        it first appears to: `translate_segments` already calls `on_batch` under
        the lock that guards its own results, so these are serialized whatever
        this function does. The lock here is for `_job_status`, which reads this
        dict from a request thread.
        """
        with _JOB_LOCK:
            state["applied"] += written
            state["refused"].extend(refused)

    def work():
        try:
            if not segments:
                log("nothing to do")
                return
            _applied, failures, _refused = do_translate(
                src, lang, cfg, segments, mode, provider=body.get("provider"),
                model=body.get("model"), batch=body.get("batch"),
                concurrency=body.get("concurrency"), progress=log, on_batch=counted,
                over_human=over_human)
            with _JOB_LOCK:
                state["failures"] = failures
                applied = state["applied"]
            log(f"applied {applied} segment(s)")
        except Exception as e:  # noqa: BLE001
            with _JOB_LOCK:
                state["error"] = str(e)
            log(f"failed: {e}")
        finally:
            _finish_job(state)

    threading.Thread(target=work, daemon=True).start()
    return {"id": state["id"], "total": len(segments), "route": route}


def serve(host="127.0.0.1", port=8787, open_browser=True):
    if host not in _LOOPBACK_BINDS:
        # Named, not merely warned about: the cross-origin check has no
        # trustworthy set of names once the bind is not loopback, so it degrades
        # to matching each request's own Host header. Saying "exposes the
        # workbench" without saying that would leave the warning true and
        # incomplete.
        print(f"warning: binding to {host} exposes the workbench, and it can spend "
              f"money through configured providers. Use 127.0.0.1 unless you mean it.\n"
              f"         the cross-origin check degrades with it: with no loopback bind to "
              f"compare against, it can only match each request's own Host header, which "
              f"does not resist DNS rebinding.")
    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{'localhost' if host == '127.0.0.1' else host}:{port}/"
    print(f"Scriptorium workbench on {url}")
    print(f"project: {os.getcwd()}")
    print("Ctrl-C to stop")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
