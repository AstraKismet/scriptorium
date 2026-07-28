"""Command line interface. Every other surface — the skill, the web UI, CI — is
a caller of these functions, so behaviour cannot diverge between them."""

import argparse
import json
import os
import re
import sys
from collections import Counter

from . import __version__
from .checks import check_segment
from .config import dump_json, load_config, load_dnt, load_glossary, write_templates
from .docio import (
    apply_terminator,
    read_document,
    split_terminator,
    write_document,
    write_document_to_stdout,
)
from .mask import repair_placeholders
from .mdparse import parse, render
from .normalize import normalize, polish_rendered
from .store import (
    StateVersionError,
    append_tm,
    load_doc,
    load_tm,
    prior_targets,
    report_path,
    save_doc,
    store_path,
    tracked,
)


def _out(msg):
    print(msg, flush=True)


def force_utf8(stream):
    """Make a diagnostic stream carry UTF-8 whatever the platform picked for it.

    On Windows an interactive console gets UTF-16 through `WriteConsoleW` and is
    fine; redirect it or pipe it and the stream falls back to the locale code
    page. Three commands emit non-ASCII — `todo` prints a literal ⟦n⟧ in its
    rules line, `check` prints validator messages containing CJK, and `render
    --out -` writes a whole translated document — so
    `lx todo doc.md --lang zh-TW > todo.json` died with UnicodeEncodeError. CI
    never saw it because the only redirect in the workflow runs on Ubuntu.

    This is for *diagnostics*. `render --out -` is a document, not a message, and
    goes out through `sys.stdout.buffer` in `docio`, which no text layer touches
    — deliberately two repairs, because that one is also a newline problem and
    this one is not.

    Guarded rather than assumed: `sys.stdout` is replaced by a stand-in under
    test and in embedded callers, and not every stand-in is a `TextIOWrapper`.
    Doing nothing is the right answer there — a stand-in has already decided how
    it encodes.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # detached, closed, or already written through
        pass


# ── extract ────────────────────────────────────────────────────────────────

def do_extract(src, lang, cfg, tone=None, reset=False):
    text, eol = split_terminator(read_document(src))
    nodes, segments = parse(text, load_dnt(cfg))

    # `prior_targets` rather than `load_doc`: extract is what migrates a state
    # file the current build refuses to read, so it must be able to read the
    # translations out of one first. It still refuses a file from a *newer*
    # build, because the next line overwrites it. `--reset` skips that read and
    # so overwrites it anyway — deliberately, and named in the message the newer
    # file raises, because "start over" is exactly what the flag means.
    prior = {} if reset else prior_targets(src, lang)

    tm = load_tm(lang)
    reused = 0
    for seg in segments:
        if seg["hash"] in prior:
            seg["target"], seg["origin"] = prior[seg["hash"]]
            seg["status"] = "translated"
            reused += 1
        elif seg["hash"] in tm:
            seg["target"], seg["origin"], seg["status"] = tm[seg["hash"]], "tm", "translated"
            reused += 1

    doc = {
        "version": __version__, "source": os.path.relpath(src), "lang": lang,
        "tone": tone or cfg.get("tone", "technical"),
        # The document's own line terminator, held here rather than in the
        # skeleton so the model and the reviewer never see it. A state file
        # written before this existed has no key, and "\n" is the right default
        # for one: text-mode reads had already deleted every CR.
        "eol": eol,
        "nodes": nodes, "segments": segments,
    }
    save_doc(src, lang, doc)
    return doc, reused


def cmd_extract(args, cfg):
    doc, reused = do_extract(args.src, args.lang, cfg, args.tone, args.reset)
    pending = sum(1 for s in doc["segments"] if s["status"] == "pending")
    _out(f"{args.src} -> {store_path(args.src, args.lang)}")
    _out(f"  segments {len(doc['segments'])} | reused {reused} | pending {pending}")


# ── todo ───────────────────────────────────────────────────────────────────

def pending_segments(doc, include_all=False, limit=0):
    out = [s for s in doc["segments"] if include_all or s["status"] == "pending"]
    return out[:limit] if limit else out


def cmd_todo(args, cfg):
    doc = load_doc(args.src, args.lang)
    glossary = load_glossary(cfg)
    items = []
    for seg in pending_segments(doc, args.all, args.limit):
        item = {"id": seg["id"], "kind": seg["kind"], "text": seg["masked"]}
        low = seg["masked"].lower()
        hints = [{"term": r["source"], "use": r["target"]} for r in glossary
                 if re.search(rf"(?<![A-Za-z]){re.escape(r['source'].lower())}(?![A-Za-z])", low)]
        if hints:
            item["glossary"] = hints
        if seg.get("issues"):
            item["fix"] = seg["issues"]
        items.append(item)
    _out(json.dumps({
        "source": doc["source"], "lang": doc["lang"], "tone": doc["tone"],
        "rules": "placeholders \u27e6n\u27e7 are opaque; copy them verbatim, "
                 "reorder if grammar needs it, never invent or drop them",
        "segments": items,
    }, ensure_ascii=False, indent=2))


# ── apply ──────────────────────────────────────────────────────────────────

def do_apply(src, lang, cfg, incoming, origin="agent"):
    doc = load_doc(src, lang)
    by_id = {s["id"]: s for s in doc["segments"]}
    applied, unknown = 0, []
    for sid, text in incoming.items():
        seg = by_id.get(sid)
        if not seg:
            unknown.append(sid)
            continue
        seg["target"] = normalize(repair_placeholders(text), lang, cfg)
        seg["status"], seg["origin"] = "translated", origin
        seg.pop("issues", None)
        applied += 1
    save_doc(src, lang, doc)
    return applied, unknown


def cmd_apply(args, cfg):
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, dict) and "segments" in data:
        data = data["segments"]
    incoming = ({d["id"]: d.get("text", d.get("target", "")) for d in data}
                if isinstance(data, list) else dict(data))
    applied, unknown = do_apply(args.src, args.lang, cfg, incoming, args.origin)
    _out(f"applied {applied} segment(s)" + (f"; unknown ids ignored: {unknown}" if unknown else ""))


# ── check ──────────────────────────────────────────────────────────────────

def do_check(src, lang, cfg, persist=True):
    doc = load_doc(src, lang)
    glossary, dnt = load_glossary(cfg), load_dnt(cfg)
    issues = []
    for seg in doc["segments"]:
        found = check_segment(seg, lang, cfg, glossary, dnt)
        if found:
            seg["issues"] = [f"{i['rule']}: {i['message']}" for i in found]
        else:
            seg.pop("issues", None)
        issues.extend(found)
    errors = [i for i in issues if i["severity"] == "error"]
    report = {
        "source": doc["source"], "lang": lang,
        "segments": len(doc["segments"]),
        "translated": sum(1 for s in doc["segments"] if s.get("target")),
        "errors": len(errors), "warnings": len(issues) - len(errors),
        "by_rule": dict(Counter(i["rule"] for i in issues)),
        "issues": issues,
    }
    if persist:
        save_doc(src, lang, doc)
        dump_json(report_path(src, lang), report)
    return report, doc


def cmd_check(args, cfg):
    report, doc = do_check(args.src, args.lang, cfg)
    if args.json:
        _out(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _out(f"{report['source']} [{report['lang']}]  "
             f"{report['translated']}/{report['segments']} translated  "
             f"{report['errors']} error(s)  {report['warnings']} warning(s)")
        by_id = {s["id"]: s for s in doc["segments"]}
        for issue in report["issues"][: args.max]:
            _out(f"  {issue['severity']:5} {issue['seg']} {issue['rule']:12} {issue['message']}")
            _out(f"        src: {by_id[issue['seg']]['masked'][:88]}")
        if len(report["issues"]) > args.max:
            _out(f"  ... {len(report['issues']) - args.max} more (use --max or --json)")
    sys.exit(1 if report["errors"] else 0)


# ── render / commit / stats ────────────────────────────────────────────────

def do_render(src, lang, cfg, fallback=False):
    doc = load_doc(src, lang)
    text, missing = render(doc, cfg, polish=lambda t: polish_rendered(t, lang, cfg),
                           fallback=fallback)
    # Here rather than in write_document so every caller gets it: the file path,
    # `--out -`, and the workbench's render endpoint are all downstream of this.
    return apply_terminator(text, doc.get("eol", "\n")), missing


def default_output(src, lang, cfg):
    pattern = cfg.get("output_pattern", "i18n/{lang}/{path}")
    return pattern.format(lang=lang, path=os.path.relpath(src).replace(os.sep, "/"),
                          name=os.path.basename(src))


def cmd_render(args, cfg):
    text, missing = do_render(args.src, args.lang, cfg, args.fallback)
    if args.out == "-":
        write_document_to_stdout(text)
        return
    out = args.out or default_output(args.src, args.lang, cfg)
    write_document(out, text)
    _out(f"wrote {out}" + (f" ({missing} untranslated)" if missing else ""))


def cmd_commit(args, cfg):
    doc = load_doc(args.src, args.lang)
    tm = load_tm(args.lang)
    records = [{"hash": s["hash"], "source": s["source"], "target": s["target"]}
               for s in doc["segments"]
               if s.get("target") and tm.get(s["hash"]) != s["target"]]
    n = append_tm(args.lang, records)
    _out(f"translation memory += {n} entries")


def cmd_stats(args, cfg):
    docs = tracked(args.lang)
    if not docs:
        _out("nothing tracked yet — run `lx extract`")
        return
    for doc in docs:
        total = len(doc["segments"])
        done = sum(1 for s in doc["segments"] if s.get("target"))
        pct = done * 100 // max(total, 1)
        _out(f"{pct:3d}% [{'#' * (pct // 5):<20}] {done}/{total}  {doc['source']} [{doc['lang']}]")


def cmd_init(args, cfg):
    created = write_templates()
    _out("initialized" + (f": {', '.join(created)}" if created else " (already set up)"))


# ── translate / repair / run ───────────────────────────────────────────────

def cmd_providers(args, cfg):
    from .providers import available
    for p in available(cfg):
        key = "no key needed" if not p["needs_key"] else (
            f"{p['key_env']} set" if p["key_present"] else f"{p['key_env']} MISSING")
        _out(f"{p['name']:12} {p['kind']:10} {p['model']:28} {p['base_url']:34} {key}")
    routing = cfg.get("routing", {})
    _out("\nrouting: " + "  ".join(f"{k}={v}" for k, v in routing.items()))


def _translate(src, lang, cfg, segments, mode, args):
    from .translate import Progress, translate_segments
    doc = load_doc(src, lang)
    if not segments:
        _out("nothing to do")
        return 0, []
    if args.dry_run:
        chars = sum(len(s["masked"]) for s in segments)
        _out(f"dry run: {len(segments)} segment(s), {chars} source characters, "
             f"mode={mode}, provider={args.provider or cfg.get('routing', {}).get(mode)}")
        return 0, []
    results, failures = translate_segments(
        segments, doc, cfg, provider_name=args.provider, mode=mode,
        batch_size=args.batch, concurrency=args.concurrency,
        progress=Progress(_out))
    applied, _ = do_apply(src, lang, cfg, results, origin=f"llm:{mode}")
    for sid, why in failures:
        _out(f"  unresolved {sid}: {why}")
    return applied, failures


def cmd_translate(args, cfg):
    doc = load_doc(args.src, args.lang)
    if args.mode == "polish":
        segments = [s for s in doc["segments"] if s.get("target") and s["kind"] in ("para", "quote", "list")]
    elif args.ids:
        wanted = set(args.ids.split(","))
        segments = [s for s in doc["segments"] if s["id"] in wanted]
    else:
        segments = pending_segments(doc, include_all=args.all, limit=args.limit)
    applied, failures = _translate(args.src, args.lang, cfg, segments, args.mode, args)
    _out(f"translated {applied} segment(s)" + (f", {len(failures)} unresolved" if failures else ""))


def cmd_repair(args, cfg):
    from .translate import failing_segments
    do_check(args.src, args.lang, cfg)
    doc = load_doc(args.src, args.lang)
    segments = failing_segments(doc, cfg)
    if not segments:
        _out("nothing failing")
        return
    _out(f"repairing {len(segments)} failing segment(s)")
    _translate(args.src, args.lang, cfg, segments, "repair", args)
    report, _ = do_check(args.src, args.lang, cfg)
    _out(f"after repair: {report['errors']} error(s), {report['warnings']} warning(s)")


def cmd_run(args, cfg):
    """extract → translate → check → repair* → render, in one command."""
    doc, reused = do_extract(args.src, args.lang, cfg, args.tone)
    pending = [s for s in doc["segments"] if s["status"] == "pending"]
    _out(f"{args.src} [{args.lang}] · {len(doc['segments'])} segments · "
         f"{reused} reused · {len(pending)} to translate")

    if pending:
        _translate(args.src, args.lang, cfg, pending, "draft", args)
    if args.polish:
        doc = load_doc(args.src, args.lang)
        prose = [s for s in doc["segments"]
                 if s.get("target") and s["kind"] in ("para", "quote", "list")]
        _out(f"polishing {len(prose)} prose segment(s)")
        _translate(args.src, args.lang, cfg, prose, "polish", args)

    from .translate import failing_segments
    rounds = args.max_rounds if args.max_rounds is not None else cfg.get("batch", {}).get("max_repair_rounds", 3)
    previous = None
    for attempt in range(rounds):
        report, _ = do_check(args.src, args.lang, cfg)
        if not report["errors"]:
            break
        bad = failing_segments(load_doc(args.src, args.lang), cfg)
        signature = {s["id"]: s.get("target") for s in bad}
        if signature == previous:
            _out("repair made no difference last round; stopping so it does not spin")
            break
        previous = signature
        _out(f"repair round {attempt + 1}/{rounds}: {len(bad)} failing segment(s)")
        _translate(args.src, args.lang, cfg, bad, "repair", args)

    report, _ = do_check(args.src, args.lang, cfg)
    _out(f"check: {report['errors']} error(s), {report['warnings']} warning(s)")
    if report["errors"] and not args.force:
        _out("not rendering while errors remain — inspect with `lx check` or fix in `lx web`, "
             "or pass --force to render anyway")
        sys.exit(1)
    out = args.out or default_output(args.src, args.lang, cfg)
    text, missing = do_render(args.src, args.lang, cfg, fallback=args.force)
    write_document(out, text)
    _out(f"wrote {out}")
    _out("review the rendered file, then `lx commit` to bank the wording in the translation memory")


def cmd_web(args, cfg):
    from .web.server import serve
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


# ── parser ─────────────────────────────────────────────────────────────────

def _add_llm_flags(p):
    p.add_argument("--provider", help="provider name from lx.config.json; overrides routing")
    p.add_argument("--batch", type=int, help="segments per request")
    p.add_argument("--concurrency", type=int, help="parallel requests")
    p.add_argument("--dry-run", action="store_true", help="report the work without calling a model")


def build_parser():
    p = argparse.ArgumentParser(prog="lx", description="Scriptorium localization pipeline")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", default="lx.config.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="scaffold config and state").set_defaults(fn=cmd_init)
    sub.add_parser("providers", help="list configured backends").set_defaults(fn=cmd_providers)

    e = sub.add_parser("extract", help="parse a document into segments")
    e.add_argument("src")
    e.add_argument("--lang", required=True)
    e.add_argument("--tone")
    e.add_argument("--reset", action="store_true")
    e.set_defaults(fn=cmd_extract)

    t = sub.add_parser("todo", help="emit pending segments as JSON")
    t.add_argument("src")
    t.add_argument("--lang", required=True)
    t.add_argument("--all", action="store_true")
    t.add_argument("--limit", type=int, default=0)
    t.set_defaults(fn=cmd_todo)

    a = sub.add_parser("apply", help="ingest translations")
    a.add_argument("src")
    a.add_argument("--lang", required=True)
    a.add_argument("--file", default="-", help="'-' reads stdin")
    a.add_argument("--origin", default="agent")
    a.set_defaults(fn=cmd_apply)

    c = sub.add_parser("check", help="validate; exit 1 on error")
    c.add_argument("src")
    c.add_argument("--lang", required=True)
    c.add_argument("--json", action="store_true")
    c.add_argument("--max", type=int, default=25)
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("render", help="rebuild the target document")
    r.add_argument("src")
    r.add_argument("--lang", required=True)
    r.add_argument("-o", "--out", help="'-' for stdout; default from output_pattern")
    r.add_argument("--fallback", action="store_true",
                   help="untranslated segments fall back to source")
    r.set_defaults(fn=cmd_render)

    m = sub.add_parser("commit", help="bank approved segments in the translation memory")
    m.add_argument("src")
    m.add_argument("--lang", required=True)
    m.set_defaults(fn=cmd_commit)

    s_ = sub.add_parser("stats", help="coverage across tracked documents")
    s_.add_argument("--lang")
    s_.set_defaults(fn=cmd_stats)

    tr = sub.add_parser("translate", help="translate segments with a configured model")
    tr.add_argument("src")
    tr.add_argument("--lang", required=True)
    tr.add_argument("--mode", choices=["draft", "polish", "repair"], default="draft")
    tr.add_argument("--ids", help="comma-separated segment ids")
    tr.add_argument("--all", action="store_true", help="include already-translated segments")
    tr.add_argument("--limit", type=int, default=0)
    _add_llm_flags(tr)
    tr.set_defaults(fn=cmd_translate)

    rp = sub.add_parser("repair", help="re-translate only segments failing check")
    rp.add_argument("src")
    rp.add_argument("--lang", required=True)
    _add_llm_flags(rp)
    rp.set_defaults(fn=cmd_repair)

    rn = sub.add_parser("run", help="extract, translate, check, repair, render")
    rn.add_argument("src")
    rn.add_argument("--lang", required=True)
    rn.add_argument("--tone")
    rn.add_argument("-o", "--out")
    rn.add_argument("--polish", action="store_true", help="second pass for fluency")
    rn.add_argument("--max-rounds", type=int, default=None)
    rn.add_argument("--force", action="store_true", help="render even if errors remain")
    _add_llm_flags(rn)
    rn.set_defaults(fn=cmd_run)

    w = sub.add_parser("web", help="open the review workbench")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8787)
    w.add_argument("--no-browser", action="store_true")
    w.set_defaults(fn=cmd_web)

    return p


def main(argv=None):
    # Before parse_args, because argparse writes usage and errors to these two.
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    try:
        args.fn(args, cfg)
    except (FileNotFoundError, StateVersionError) as e:
        print(f"lx: {e}", file=sys.stderr)
        sys.exit(2)
    except BrokenPipeError:
        os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
