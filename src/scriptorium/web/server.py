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
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..cli import default_output, do_apply, do_check, do_extract, do_render, pending_segments
from ..config import load_config
from ..docio import write_document
from ..providers import available
from ..store import append_tm, load_doc, load_tm, tracked

STATIC = os.path.join(os.path.dirname(__file__), "static")


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

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path.startswith("/api/"):
                return self._send(200, self._get(url.path, q))
            return self._static(url.path)
        except Exception as e:  # noqa: BLE001 - surface to the UI
            return self._send(400, {"error": str(e)})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            return self._send(200, self._post(url.path, self._body()))
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})

    def _static(self, path):
        """Serve a file from the static root, and nothing outside it.

        The guard this replaces decided on a string before the filesystem had a
        say: it normalized with `posixpath` and rejected a leading "..". Three
        things get past that, so there are now three answers.

        Percent-decoding happens first, because "%2e%2e%2f" walks up while
        looking inert. It also un-breaks any asset whose name holds a space or a
        non-ASCII character, which used to 404 for the same reason.

        A backslash is then a separator on every platform. `posixpath` does not
        treat it as one and Windows' `open` does, so
        `x\\..\\..\\..\\..\\..\\pyproject.toml` passed the old guard untouched
        and then resolved five levels above the static root, into the repository.
        Rewriting it here rather than only where it is exploitable keeps the rule
        — and the test that pins it — identical on both platforms.

        The containment check is on the *resolved* path, which is the one that
        actually decides: comparing the join as a string cannot see a symlink,
        and `..` inside the join is only inert after resolution.

        Loopback binding bounds the exposure and does not remove it: every local
        process could read anything this user could, no remote one could.
        """
        rel = posixpath.normpath(unquote(path).replace("\\", "/").lstrip("/"))
        if rel in ("", "."):
            rel = "index.html"
        root = os.path.realpath(STATIC)
        full = os.path.realpath(os.path.join(root, rel))
        if full != root and not full.startswith(root + os.sep):
            return self._send(403, b"forbidden", "text/plain")
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
        cfg = load_config()
        if path == "/api/state":
            return {
                "version": __version__,
                "cwd": os.getcwd(),
                "targets": cfg.get("targets", []),
                "providers": available(cfg),
                "routing": cfg.get("routing", {}),
                "docs": [{
                    "source": d["source"], "lang": d["lang"],
                    "total": len(d["segments"]),
                    "done": sum(1 for s in d["segments"] if s.get("target")),
                } for d in tracked()],
                "candidates": _scan_sources(cfg),
            }
        if path == "/api/doc":
            doc = load_doc(q["src"], q["lang"])
            report, doc = do_check(q["src"], q["lang"], cfg, persist=False)
            issues = {}
            for i in report["issues"]:
                issues.setdefault(i["seg"], []).append(i)
            return {
                "source": doc["source"], "lang": doc["lang"], "tone": doc["tone"],
                "report": {k: report[k] for k in ("segments", "translated", "errors", "warnings", "by_rule")},
                "segments": [{
                    "id": s["id"], "kind": s["kind"], "status": s["status"],
                    "origin": s.get("origin"), "source": s["masked"],
                    "target": s.get("target") or "",
                    "issues": issues.get(s["id"], []),
                } for s in doc["segments"]],
            }
        if path == "/api/preview":
            text, missing = do_render(q["src"], q["lang"], cfg, fallback=True)
            return {"text": text, "missing": missing,
                    "default_out": default_output(q["src"], q["lang"], cfg)}
        raise ValueError(f"unknown endpoint {path}")

    # -- write ------------------------------------------------------------
    def _post(self, path, body):
        cfg = load_config()
        src, lang = body.get("src"), body.get("lang")
        if path == "/api/extract":
            doc, reused = do_extract(src, lang, cfg, body.get("tone"), body.get("reset", False))
            return {"segments": len(doc["segments"]), "reused": reused}
        if path == "/api/save":
            applied, unknown = do_apply(src, lang, cfg, body["targets"], origin="human")
            return {"applied": applied, "unknown": unknown}
        if path == "/api/check":
            report, _ = do_check(src, lang, cfg)
            return report
        if path == "/api/translate":
            return _translate_job(src, lang, cfg, body)
        if path == "/api/job":
            return _job_status(body["id"])
        if path == "/api/render":
            text, missing = do_render(src, lang, cfg, fallback=body.get("fallback", False))
            out = body.get("out") or default_output(src, lang, cfg)
            write_document(out, text)
            return {"wrote": out, "missing": missing}
        if path == "/api/commit":
            doc = load_doc(src, lang)
            tm = load_tm(lang)
            recs = [{"hash": s["hash"], "source": s["source"], "target": s["target"]}
                    for s in doc["segments"]
                    if s.get("target") and tm.get(s["hash"]) != s["target"]]
            return {"committed": append_tm(lang, recs)}
        raise ValueError(f"unknown endpoint {path}")


# ── background translation jobs ────────────────────────────────────────────

_JOBS = {}
_JOB_LOCK = threading.Lock()


def _translate_job(src, lang, cfg, body):
    """Translation runs off-thread so the UI stays responsive on slow local models."""
    from ..translate import Progress, failing_segments, translate_segments

    doc = load_doc(src, lang)
    mode = body.get("mode", "draft")
    if body.get("ids"):
        wanted = set(body["ids"])
        segments = [s for s in doc["segments"] if s["id"] in wanted]
    elif mode == "repair":
        segments = failing_segments(doc, cfg)
    elif mode == "polish":
        segments = [s for s in doc["segments"]
                    if s.get("target") and s["kind"] in ("para", "quote", "list")]
    else:
        segments = pending_segments(doc)

    job_id = f"job{len(_JOBS) + 1}"
    state = {"id": job_id, "done": False, "log": [], "applied": 0,
             "failures": [], "error": None, "total": len(segments)}
    with _JOB_LOCK:
        _JOBS[job_id] = state

    def log(msg):
        with _JOB_LOCK:
            state["log"].append(msg)

    def work():
        try:
            if not segments:
                log("nothing to do")
                return
            results, failures = translate_segments(
                segments, doc, cfg, provider_name=body.get("provider"), mode=mode,
                batch_size=body.get("batch"), concurrency=body.get("concurrency"),
                progress=Progress(log))
            applied, _ = do_apply(src, lang, cfg, results, origin=f"llm:{mode}")
            with _JOB_LOCK:
                state["applied"], state["failures"] = applied, failures
            log(f"applied {applied} segment(s)")
        except Exception as e:  # noqa: BLE001
            with _JOB_LOCK:
                state["error"] = str(e)
            log(f"failed: {e}")
        finally:
            with _JOB_LOCK:
                state["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"id": job_id, "total": len(segments)}


def _job_status(job_id):
    with _JOB_LOCK:
        state = _JOBS.get(job_id)
        return dict(state) if state else {"error": "no such job"}


def _scan_sources(cfg):
    """Files matching config `sources` that are not yet tracked."""
    import glob
    seen = {(d["source"], d["lang"]) for d in tracked()}
    out = []
    for pattern in cfg.get("sources", []):
        for path in sorted(glob.glob(pattern, recursive=True)):
            rel = os.path.relpath(path).replace(os.sep, "/")
            for lang in cfg.get("targets", []):
                if (rel, lang) not in seen:
                    out.append({"source": rel, "lang": lang})
    return out[:200]


def serve(host="127.0.0.1", port=8787, open_browser=True):
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: binding to {host} exposes the workbench, and it can spend "
              f"money through configured providers. Use 127.0.0.1 unless you mean it.")
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
