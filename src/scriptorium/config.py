"""Project configuration, glossary, do-not-translate list, and style sheet."""

import json
import os
import re
import stat
import urllib.parse

STATE = ".lx"

#: The register a document is in when nobody said otherwise — and the value the
#: translation-memory key treats as its null, so that everything banked before
#: registers existed keeps answering (see :func:`store.key_tone`). Named once
#: rather than written as a literal in both places: the two have to be the same
#: string, and a drift between them silently invalidates the whole memory.
DEFAULT_TONE = "technical"

#: The glossary's own first two lines. Named once because two places write them:
#: `write_templates` scaffolding a fresh project, and `lx terms --append` creating
#: the file when the project never ran `lx init`. A second, drifted copy of a
#: header is the kind of thing nobody notices until two projects disagree about
#: what column three means.
GLOSSARY_HEADER = (
    "source,target,forbidden,severity\n"
    "# forbidden is ;-separated; severity is error or warn\n"
    "# a row with an empty target is a proposal from `lx terms` and enforces\n"
    "# nothing until someone writes the rendering in\n"
)

#: The style sheet's own first lines. A comments-only file, so a scaffolded
#: project has a style sheet that teaches its own format and injects nothing —
#: the same thing `config/dnt.txt` does, and the reason is the same: a
#: hand-authored format nobody can discover is a format nobody writes. The
#: example is inside the comments rather than beside them, because a live
#: example in a scaffolded file would be sent to the model on the first run.
STYLE_HEADER = """\
# This project's style sheet: how the narrator sounds, and how each character
# speaks. Everything here reaches the translator except lines starting with #,
# which are notes to yourself.
#
# Lines before the first [name] block are sent with EVERY request — keep them
# to the narrator and to rules that hold everywhere.
#
# A [name] block is sent only when a batch mentions that name, so the cast can
# be as large as the book needs. List the forms the name takes in the SOURCE
# text, separated by commas. `lx terms SRC --lang L` proposes the cast for you.
#
# A line of your own that looks like [this] would be read as a block header.
#
# Example — the same file with the # removed from the last five lines:
#
# The narration is close third person, past tense, anchored on Eleanor.
#
# [Eleanor Vance, Eleanor, Miss Vance]
# She says 您 to her father and to Mr Ashcombe, 你 to her sister.
# Her diction is precise and a little cold; no 呢, no 嘛.
"""

#: Two limits rather than one total, because the sheet's two halves have
#: different costs. The preamble rides on **every** request — some eighty of
#: them for a 100k-word novel — while a `[name]` block rides only on the batches
#: that mention it. One total limit would have to be tight enough for the
#: always-on half, which would cap the cast at the size of a short story and
#: throw away the reason the format has blocks at all.
#:
#: There is deliberately **no cap on the number of blocks one request may
#: carry.** The injected set is bounded by the names the batch itself contains,
#: so a request carrying forty of them is a request about forty characters —
#: which is exactly when the notes are wanted. `_glossary_hints` has always
#: worked this way. Measured ratios are in `docs/decisions.md`, 2026-08-02.
STYLE_PREAMBLE_MAX = 2000
STYLE_BLOCK_MAX = 800

#: A block header: a line that is nothing but a bracketed, comma-separated list
#: of the names this block answers to. Anchored on the stripped line, so a
#: bracketed line indented by an editor is still a header — and so a bracket
#: *inside* a sentence is not.
_STYLE_BLOCK_RE = re.compile(r"^\[(.+)\]$")


class ConfigError(ValueError):
    """A configuration value this project will not write, or a key that addresses nothing.

    Raised rather than warned, and caught in `cli.main` for exit 2 — the
    treatment `StyleSheetError` already gets, for the same reason. Everything it
    guards is checked *before* anything is written, so a refused value never
    reaches the file and the configuration on disk is still the one that was
    there: a half-written config is worse than an unwritten one, because the run
    it breaks is the next one rather than this one.
    """


class StyleSheetError(ValueError):
    """The style sheet could not be read, and nothing of it reached the model.

    Raised rather than warned, and caught in `cli.main` for exit 2. The
    alternative is a sheet that silently half-applies: a run that translates a
    whole book under voice instructions the person believes are in force and
    which were dropped at load. That failure is invisible until someone reads
    the output, which for a novel is hours later.
    """


#: The plain-text format's knobs, all three of them heuristics. They live here
#: rather than in `textparse.py` for two reasons: `lx init` scaffolds
#: `DEFAULT_CONFIG` verbatim, so a knob that is not here is a knob nobody
#: discovers; and `textparse` reads these as its own fallbacks, so there is one
#: literal rather than two that drift. `config.py` imports nothing from this
#: package, which is what makes that direction of the dependency safe.
#:
#: Invariant 4 is why they are configuration at all: encoding detection, chapter
#: detection and paragraph segmentation are heuristics, and a heuristic is a
#: project's call rather than a rule `checks.py` may enforce.
TEXT_DEFAULTS = {
    # Ordered, and the order is the whole decision: tried with `errors="strict"`,
    # first success wins, after a BOM sniff that decides on its own. Every name
    # must be BOM-neutral — never `utf-16` or `utf-8-sig`, which write a mark of
    # their own — because a mark that survives is what keeps the skeleton exact.
    #
    # Measured 2026-08-02, and every entry earns its place by a measurement:
    #
    # `cp950`, not `big5`. Python's `big5` codec rejects 裏, 碁, 恒 and 墻, which
    # are ordinary characters in Windows-authored Traditional Chinese, so a real
    # Big5 novel containing one of them failed every DBCS candidate and was read
    # by `cp1252` as Latin-1 gibberish — the worst outcome available, because it
    # is durable: the mojibake is hashed and banked in the translation memory.
    # `cp950` is a superset and decodes all four.
    #
    # `shift_jis` before the Chinese candidates. It fails on the Big5 and GBK
    # samples, so it costs them nothing, and without it `gbk` swallows Japanese.
    #
    # `gbk`, not `gb18030`. The superset argument that wins for cp950 loses here:
    # gb18030 is a near-total catch-all for double-byte input and accepted the
    # Big5, Shift-JIS and Latin-1 samples too.
    #
    # `cp1252` last and still present. A Windows-authored English novel with
    # smart quotes is the commonest non-UTF-8 source there is, and dropping it
    # refuses that file outright. It accepts every *standard* Big5 and GB2312
    # byte stream — none of its five undefined bytes appears in either range — so
    # it is a catch-all rather than a near one, and only its position and the
    # cp950 repair above keep a Chinese novel from reaching it.
    #
    # What is left is the irreducible overlap: simplified Chinese reads as Big5,
    # and a Latin-1 European source reads as Shift-JIS. Both are announced by
    # `lx extract`, which prints the winning encoding, rather than fixed by a
    # cleverer rule — refusing on ambiguity was the alternative and it refuses
    # every ordinary Big5, GBK and Shift-JIS novel, since `cp1252` also accepts
    # each of them. A project reorders this list; that is what it is for.
    "encodings": ["utf-8", "shift_jis", "cp950", "gbk", "cp1252"],
    # `auto`, `blank-line`, `line`, or `indent`. Plain text arrives in three
    # shapes and only two of them can be told apart safely, so auto decides
    # between those two and the third is named rather than guessed: if some blank
    # line separates two runs of text, paragraphs are blank-line separated and a
    # hard-wrapped paragraph is one segment; otherwise every line is a paragraph,
    # which is how a great many .txt novels are written. `indent` is the third —
    # hard-wrapped with an indent marking each new paragraph and no blank lines
    # anywhere — and a project holding one says so, because the test that would
    # detect it (some lines indented, some not) is also true of a per-line file
    # with one indented line in it, where guessing wrong joins the whole book.
    "paragraph_mode": "auto",
    # Case-insensitive, matched against a block that is exactly one line, with
    # its surrounding whitespace stripped. Two patterns rather than one because
    # the keywords split into two groups with different false-positive risks.
    #
    # `chapter`, `part`, `book` and `volume` all open ordinary English sentences
    # — "Part of her wanted to run." — so they require a number after them. The
    # second group is words that essentially only ever stand as a title.
    #
    # Both directions are cheap, which is what makes a heuristic acceptable here:
    # a miss leaves a chapter title as an ordinary paragraph, still translated,
    # and a false positive costs the `heading` kind and its memory context. What
    # is *not* cheap is editing these patterns later — see `docs/decisions.md`,
    # 2026-08-02: `context` is the kind, so a block that changes kind orphans its
    # banked wording while its text stays byte-identical.
    "chapter_patterns": [
        r"(?:chapter|part|book|volume)\b[\s.:—–-]*"
        r"(?:\d{1,4}|[ivxlcdm]{1,8}|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
        r"first|second|third|last|final)\b.{0,30}$",
        r"(?:prologue|epilogue|interlude|prelude|foreword|preface|afterword|appendix)\b.{0,30}$",
    ],
}

#: The pipeline stages a `routing` entry may name: the ones that dispatch to a
#: backend. One tuple rather than four literals, because `translate`'s `mode`,
#: the `--mode` choices, `DEFAULT_CONFIG["routing"]` and `lx routing set`'s
#: validator all have to agree about what a stage is, and a fourth spelling is
#: how a stage silently stops being routable.
#:
#: Review and audit are on the roadmap as *workflow* stages and are deliberately
#: not here. A stage earns an entry by dispatching to a model; adding one that
#: does not would make `lx routing set` write a key nothing reads.
ROUTING_STAGES = ("draft", "polish", "repair")

#: The configuration keys whose value is a path. Named in one place because
#: invariant 11 binds all of them together the day anything writes configuration
#: over HTTP: each is a path read out of a configuration file, and each is
#: trusted today on that invariant's own stated exception — a person typing at a
#: terminal, which `lx config set` still is. On the day an HTTP writer appears,
#: either every key here is confined at *use* time or none of them is writable
#: over HTTP; `output_pattern` is confined on the result of formatting it, never
#: on the pattern, because `../../{path}` is only decidable after interpolation.
#: A fifth path key added anywhere else is the one that confinement misses.
PATH_VALUED_KEYS = ("glossary", "dnt", "style", "output_pattern")

#: What `set_in` and `unset_in` return for a key the file did not have. A
#: sentinel rather than `None`, because a stored `null` is a value somebody wrote.
MISSING = object()

DEFAULT_CONFIG = {
    "source_lang": "en",
    "targets": ["zh-TW"],
    "tone": DEFAULT_TONE,
    "glossary": "config/glossary.csv",
    "dnt": "config/dnt.txt",
    "style": "config/style.txt",
    "sources": ["docs/**/*.md"],
    "output_pattern": "i18n/{lang}/{path}",
    "length_ratio": {"zh-TW": [0.25, 1.20]},
    "normalize": {"zh-TW": ["punct", "pangu", "collapse_space"]},
    "lexicon_extra": {},
    "checks_disabled": [],
    # Which parser reads a document, and the per-format knobs. `map` is the
    # explicit override on the built-in extension table in `formats.py` —
    # `{".nfo": "text"}` — and is the only override there is, deliberately: a
    # `--format` flag would let one invocation disagree with the next about what
    # a file is, and the format is frozen onto the document at extract.
    "formats": {"map": {}, "text": dict(TEXT_DEFAULTS)},
    # `lx terms` is a heuristic, so its knobs are configuration rather than a
    # fixed table — the same rule chapter detection follows. Invariant 4 keeps
    # judgement out of `checks.py`; this command proposes rather than decides,
    # and what counts as a proposal is the project's call.
    "terms": {
        # 2, not 3: a name seen once is not enforceable terminology, and anything
        # higher silently drops the secondary characters a novel is full of. The
        # output is a list a person edits, so a spare row costs one keystroke
        # and a missing one costs the whole point of the command.
        "min_count": 2,
        # A full stop after one of these does not end a sentence, so the name
        # that follows keeps its mid-sentence evidence. `Mr. Darcy` is the case:
        # without this, a character named only after an honorific never appears
        # anywhere this command would call mid-sentence.
        "abbreviations": ["Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Sr", "Jr",
                          "Mt", "Capt", "Lt", "Sgt", "Col", "Gen", "Rev", "Hon"],
        # The one English word that is capitalized everywhere and is never a
        # term. Kept as a list rather than a rule because the next entry a
        # project needs is a project's own.
        "stopwords": ["I", "I'm", "I'll", "I've", "I'd"],
    },
    "providers": {
        "local": {
            "kind": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b-instruct",
            "api_key_env": "",
            "timeout": 300,
            "temperature": 0.2,
        },
        "lmstudio": {
            "kind": "openai",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key_env": "",
            "timeout": 300,
            "temperature": 0.2,
        },
        "openai": {
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "timeout": 120,
            "temperature": 0.2,
        },
        "claude": {
            "kind": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-sonnet-4-6",
            "api_key_env": "ANTHROPIC_API_KEY",
            "timeout": 120,
            "temperature": 0.2,
        },
    },
    # A value is a provider name, or `{"provider": …, "model": …}` when the stage
    # wants a different model at the same endpoint — a draft pass on something
    # cheap and a polish pass on something strong, without a duplicate provider
    # entry whose `base_url`, `api_key_env` and timeout are copies that drift.
    # The bare string is what ships, and stays valid: every configuration in
    # existence is written that way. `lx routing set` writes both shapes.
    "routing": {"draft": "local", "polish": "local", "repair": "local"},
    # `context`: how many segments either side travel with each request item as
    # read-only source, so that a pronoun, a speaker or a tense has something to
    # resolve against. `0` turns the feature off entirely, including the
    # paragraph of the system prompt that describes it.
    #
    # The default is nearly free because a neighbour already in the payload is
    # referenced by id rather than repeated: only the two edges of a batch carry
    # text, and a retry — where there is no batch to borrow from — carries both
    # sides. Widening it is not free and the growth is linear: each extra
    # segment of window inlines two more at every batch edge and two more on
    # every retried segment.
    "batch": {"size": 25, "concurrency": 2, "max_repair_rounds": 3, "context": 1},
}


def canonical_tone(value):
    """What counts as the same register: the tone, stripped and lowercased.

    One normalizer for the two readers that must agree — `translate` picks the
    language brief with it, `store` builds the memory key from it — because
    ``--tone Literary`` and ``--tone literary`` naming two registers, and
    therefore two sets of banked wording, is a split nobody would ever find.

    It decides sameness only. The user's own string still reaches the model on
    the ``Tone:`` line, and an unrecognized one still selects the default
    register's brief, so this narrows nothing the field could say before.
    """
    return str(value or "").strip().lower() or DEFAULT_TONE


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is None:
            raise FileNotFoundError(path)
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj, create_mode=None):
    """Write JSON atomically, with LF whatever platform ran the command.

    The terminator is a *choice* here, not an invariant: `docio` exists because
    invariant 2a claims the bytes of user documents, and it explicitly excludes
    the files this project writes for itself. What argues for it anyway is that
    two of these land in someone's repository — `lx.config.json` from `lx init`,
    and the `.lx/` state for anyone who tracks it — so leaving the default meant
    one command producing a different tree depending on the machine that ran it,
    and the whole diff showing up the first time two of them shared a project.
    One keyword per site is a cheap price for that, and it costs nothing to read.

    ``create_mode`` is for the one file that sits next to a person's secrets:
    `lx.config.json`, which holds no credential by construction but does hold
    `base_url`, and which a writable configuration turns into something worth
    ordinary care. With it, three things change.

    *The temporary file is created with `O_EXCL` and that mode, in one call.* A
    `chmod` after `open` leaves a window where the bytes exist under whatever
    the umask decided, and the umask can only ever *remove* bits from `0o600`,
    never widen them. `O_EXCL` — after removing a stale `.tmp` from a crashed
    write — also refuses to write through a link planted at a name this
    function's own predictability gives away.

    *An existing file keeps the mode it already has.* `os.replace` gives the
    destination the temporary file's mode, so without this a config a person
    deliberately made group-readable would silently become owner-only on the
    first `lx config set`. Owner-only is for *creation*; tightening a mode
    somebody chose by hand is a rewrite nobody asked for.

    *The temporary file never survives a failure.* Unguarded, an exception
    between the write and the replace left the whole configuration in a
    world-readable `lx.config.json.tmp` indefinitely. `cli.append_glossary_rows`
    already pays this; `dump_json` did not.

    **On Windows the mode reaches only the read-only attribute, and owner-only
    is not achieved.** A file's protection there is the DACL it inherits from
    its directory — private in practice under a user profile, not private at all
    on a world-writable share. No ACL surgery is attempted: pywin32 is a
    compiled extension and invariant 1 refuses it, and a hand-rolled `ctypes`
    equivalent would be security code testable on one CI runner in four. What
    Windows does get is the exclusive create and the atomic replace.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if create_mode is not None and os.path.islink(path):
        # `os.replace` would swap the link itself for a regular file and quietly
        # detach a layout somebody built on purpose.
        raise ConfigError(
            f"{path} is a symbolic link. Edit the file it points at instead.")
    tmp = path + ".tmp"
    try:
        if create_mode is None:
            handle = open(tmp, "w", encoding="utf-8", newline="\n")
        else:
            try:
                os.remove(tmp)
            except OSError:
                pass
            handle = os.fdopen(
                os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_mode),
                "w", encoding="utf-8", newline="\n")
        with handle as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if create_mode is not None and os.path.exists(path):
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp, path)
    except BaseException:
        # Every failure, not only `OSError`. Measured: a lone surrogate escape in
        # a hand-edited file raises `UnicodeEncodeError` out of `json.dump`, and
        # a Ctrl-C between the write and the replace raises `KeyboardInterrupt`
        # — neither is an `OSError`, and both left the whole configuration in a
        # world-readable `.tmp`, which is the exact residue this guard exists to
        # prevent. Re-raised unchanged; this only cleans up.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def printable_url(url):
    """A base URL as it is safe to print: no userinfo, no query.

    Neither is writable through `lx config set` any more, but a hand-edited file
    can hold both and a proxy that takes `?key=` is a real shape. The host is
    what a person needs to see — it answers "where is my document going" — and
    the rest is dropped rather than trusted.

    It lives here rather than in `cli.py` because `providers.available` is the
    other display surface and feeds both `lx providers` and `/api/state`. One
    function, or the two commands disagree about what is printable — measured,
    and the disagreement was `lx providers` showing in full what
    `lx config get` had just masked.
    """
    if not isinstance(url, str):
        return url
    try:
        parsed = urllib.parse.urlsplit(url)
        carries = bool(parsed.username or parsed.password or parsed.query)
    except ValueError:
        return url
    if not carries:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, host, parsed.path, "…" if parsed.query else "", ""))


# ── addressing ─────────────────────────────────────────────────────────────

def split_key(key):
    """A dotted key as its segments, refusing the spellings that address nothing."""
    if not isinstance(key, str) or not key.strip():
        raise ConfigError(
            "give a key, as in `batch.size` or `providers.local.model`. "
            "`lx config get` with no key prints the whole merged configuration.")
    parts = key.split(".")
    if any(not part for part in parts):
        raise ConfigError(
            f"{key!r} has an empty segment. Keys are dotted names, as in "
            f"`providers.local.model` — and a key whose own name contains a dot "
            f"cannot be spelled that way at all, so write its block instead: "
            f"""`lx config set formats.map '{{".nfo": "text"}}'`.""")
    return parts


def get_in(data, parts):
    """The value at a dotted key, or `KeyError` naming the segment that ran out."""
    cur = data
    for i, part in enumerate(parts):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(".".join(parts[:i + 1]))
        cur = cur[part]
    return cur


def set_in(data, parts, value):
    """Write `value` at a dotted key, opening the blocks above it. Returns the old value.

    A block that is opened is created empty; a segment that already holds
    something which is *not* a block is refused rather than replaced, because
    replacing it is how `lx config set routing.draft.model X` would silently
    throw away the provider name that `routing.draft` was.
    """
    cur = data
    for i, part in enumerate(parts[:-1]):
        below = cur.get(part)
        if below is None:
            below = cur[part] = {}
        elif not isinstance(below, dict):
            prefix = ".".join(parts[:i + 1])
            raise ConfigError(
                f"{prefix} holds a value, not a block, so {'.'.join(parts)} addresses "
                f"nothing inside it. Write {prefix} itself, or use the command that "
                f"owns it — a routing entry is "
                f"`lx routing set <stage> <provider>[:<model>]`.")
        cur = below
    old = cur.get(parts[-1], MISSING)
    cur[parts[-1]] = value
    return old


def unset_in(data, parts):
    """Remove a dotted key and every block it emptied. Returns the old value or `MISSING`.

    The blocks go because the file is read by people: unsetting the only key in
    `batch` would otherwise leave `"batch": {}` behind, and an empty block reads
    as a decision somebody made rather than as the absence of one.
    """
    chain, cur = [data], data
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            return MISSING
        chain.append(cur)
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return MISSING
    old = cur.pop(parts[-1])
    for i in range(len(chain) - 1, 0, -1):
        if chain[i]:
            break
        del chain[i - 1][parts[i - 1]]
    return old


# ── routing ────────────────────────────────────────────────────────────────

def route_entry(cfg, stage):
    """What a stage's `routing` entry *says*, as ``(provider, model)``.

    The model is `""` when the entry names none, which is not the same as the
    provider having none: this reports what was written, and `resolve_route` is
    what fills the rest in. `lx routing show` needs both to tell an override
    from a default.

    A stage with no entry of its own falls back to `draft`'s and then to
    `local`, which is what `translate_segments` did before this was a function
    and what several hand-written configurations rely on. A stage that *has* an
    entry and whose entry is malformed is an error instead of that same
    fallback: an empty string used to route the document to whatever `draft`
    named, and a document arriving at an endpoint nobody chose is the hazard the
    `base_url` half of this command exists around.
    """
    routing = cfg.get("routing") or {}
    if not isinstance(routing, dict):
        raise ConfigError("`routing` is a block of stage → provider entries.")
    source = stage if stage in routing else "draft"
    value = routing.get(source, "local")
    if isinstance(value, dict):
        provider, model = value.get("provider"), value.get("model") or ""
    else:
        provider, model = value, ""
    if not isinstance(provider, str) or not provider.strip():
        raise ConfigError(
            f"routing.{source} names no provider. An entry is a provider name, as in "
            f'"local", or {{"provider": "local", "model": "…"}} — '
            f"`lx routing set {source} <provider>[:<model>]` writes either.")
    if not isinstance(model, str):
        raise ConfigError(f"routing.{source}.model is a model id, as text.")
    return provider.strip(), model.strip()


def resolve_route(cfg, stage, provider=None, model=None):
    """Which backend and which model a run of `stage` uses: ``(provider, model)``.

    Most specific first — the caller's `model`, then the routing entry's, then
    the provider's own. One function for the three callers: `translate.py`
    builds a provider from it, `cli.py` prints it in `lx routing show` and in
    the dry-run line, and `web/server.py` projects it into `/api/state`. A
    second site resolving this on its own is how the workbench comes to disagree
    with the CLI about which model just spent an hour on a chapter.

    **A `provider` override drops the entry's model.** `--provider openai` on a
    stage routed to `{"provider": "local", "model": "qwen2.5:14b-instruct"}`
    must not ask OpenAI for a Qwen build — a model id belongs to the backend
    that serves it. The caller's own `model` survives, because that one was
    typed for this run and for this provider.
    """
    name, routed = route_entry(cfg, stage)
    if provider and provider != name:
        name, routed = provider, ""
    spec = (cfg.get("providers") or {}).get(name)
    if not isinstance(spec, dict):
        spec = {}
    return name, str(model or routed or spec.get("model") or "")


def _merge(base, override):
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path="lx.config.json"):
    """User config layered over defaults, so new keys never break old projects."""
    return _merge(DEFAULT_CONFIG, load_json(path, {}))


def load_glossary(cfg):
    path = cfg.get("glossary", "config/glossary.csv")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if i == 0 and parts[0].lower() == "source":
                continue
            if len(parts) < 2:
                continue
            rows.append({
                "source": parts[0],
                "target": parts[1],
                "forbidden": [x for x in (parts[2].split(";") if len(parts) > 2 and parts[2] else []) if x],
                "severity": parts[3] if len(parts) > 3 and parts[3] else "error",
            })
    return rows


def load_dnt(cfg):
    path = cfg.get("dnt", "config/dnt.txt")
    if not os.path.exists(path):
        return []
    terms = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return sorted(set(terms), key=len, reverse=True)


def load_style(cfg):
    """The project's voice notes: ``(preamble, blocks)``. No file means ``("", [])``.

    ``preamble`` is everything before the first ``[name]`` header — the
    narrator, and whatever holds everywhere — and it is sent with every request.
    ``blocks`` is a list of ``{"names": [...], "notes": str}``, each sent only
    when a batch mentions one of its names.

    **Nothing inside a block is parsed.** The header says who the block answers
    to and that is the whole of the structure; the body is the person's own
    prose, handed to the model as written. That line is where invariant 4 sits:
    deciding *whether* to send a block is mechanical — does this text contain
    this name — while deciding what good narration sounds like is judgement, and
    a format with `register:` and `address:` fields would have put the second
    one inside `config.py`. Fields were the losing alternative;
    `docs/decisions.md`, 2026-08-02.

    Comments are stripped before anything is measured, so a heavily annotated
    sheet is not refused for prose nobody will ever send.
    """
    path = cfg.get("style", "config/style.txt")
    if not os.path.exists(path):
        return "", []
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except UnicodeDecodeError:
        # A style sheet for a zh-TW project is full of Chinese, and an editor on
        # Windows will still offer to save it as cp950. Named rather than left
        # as a traceback, because "which file" is the whole of the fix.
        raise StyleSheetError(
            f"{path} is not valid UTF-8 — re-save it as UTF-8 and run again") from None

    preamble, blocks = [], []
    names, body = None, []

    def close():
        if names is not None:
            blocks.append({"names": names, "notes": "\n".join(body).strip()})

    for lineno, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        header = _STYLE_BLOCK_RE.match(stripped)
        if not header:
            (body if names is not None else preamble).append(line)
            continue
        close()
        names = [n.strip() for n in header.group(1).split(",") if n.strip()]
        body = []
        if not names:
            raise StyleSheetError(
                f"{path} line {lineno}: a block header needs at least one name, "
                f"as in [Eleanor Vance, Eleanor]")
    close()

    preamble = "\n".join(preamble).strip()
    if len(preamble) > STYLE_PREAMBLE_MAX:
        raise StyleSheetError(
            f"{path}: the lines before the first [name] block are "
            f"{len(preamble)} characters and the limit is {STYLE_PREAMBLE_MAX} — "
            f"they ride on every request. Move what belongs to one character "
            f"into a [name] block, which is only sent where that name appears.")
    for block in blocks:
        if len(block["notes"]) > STYLE_BLOCK_MAX:
            raise StyleSheetError(
                f"{path}: the [{', '.join(block['names'])}] block is "
                f"{len(block['notes'])} characters and the limit is "
                f"{STYLE_BLOCK_MAX} — split it, or cut it to what changes the "
                f"wording.")
    # A block whose body is empty after comments are stripped is a header
    # somebody has not filled in yet. Dropped rather than refused, on the same
    # ground as a glossary row with an empty target: an unfinished line in a
    # hand-maintained file must be silent, never harmful.
    return preamble, [b for b in blocks if b["notes"]]


def write_templates():
    """Idempotent scaffolding for a fresh project.

    `newline="\\n"` for the same reason as `dump_json`: these two files are
    edited and committed by the user, so a scaffolder that emits CRLF on one
    machine and LF on another hands them a diff they did not make.
    """
    created = []
    if not os.path.exists("lx.config.json"):
        # Owner-only from the moment it exists, so that `lx config set` is not the
        # first command in the file's life to think about its mode. It holds no
        # credential by construction (invariant 6) and it does hold `base_url`,
        # which is where a document goes. See `dump_json` for what Windows does
        # and does not get out of this.
        dump_json("lx.config.json", DEFAULT_CONFIG, create_mode=0o600)
        created.append("lx.config.json")
    # No `.lx/docs` any more: document state is one SQLite database, created on
    # first write by `store._connect`. `.lx/reports` is still a directory of JSON
    # files, because a report is a rebuildable projection rather than state.
    for d in (STATE, os.path.join(STATE, "reports"), "config"):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists("config/glossary.csv"):
        with open("config/glossary.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write(GLOSSARY_HEADER)
        created.append("config/glossary.csv")
    if not os.path.exists("config/dnt.txt"):
        with open("config/dnt.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write("# verbatim terms, one per line; longest match wins\n")
        created.append("config/dnt.txt")
    if not os.path.exists("config/style.txt"):
        with open("config/style.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write(STYLE_HEADER)
        created.append("config/style.txt")
    return created
