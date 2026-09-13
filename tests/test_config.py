"""Writing configuration, and routing a stage to a model.

Two capabilities and one promise. The capabilities are `lx config`, which edits
`lx.config.json` without a text editor, and `lx routing`, which points a stage at
a backend and optionally at a model of its own. The promise is that neither of
them can put a credential into a file this project's own scaffolder expects to be
committed — invariant 6 held from the other side, by the writer rather than by
the reader.

Three groups of tests, and each exists because of a measured way the feature
fails without it:

* **the resolver**, because `--provider`, the routing entry and the provider spec
  each name a model and only one of them can win. Three call sites resolved this
  independently before it was a function, and the workbench and the CLI can
  disagree about which model just spent an hour on a chapter;
* **the writer**, because a configuration is a hand-maintained file: a key from a
  newer build has to survive an older build's write, a refusal has to leave every
  byte alone, and an interrupted write must not leave the whole file in a
  world-readable `.tmp`;
* **the credential rules**, because the field that should hold the *name* of an
  environment variable is exactly the field somebody pastes a key into. Shape
  alone does not decide it — `hf_…`, `ghp_…` and every hex token are legal
  identifiers — so the rules are tested against the formats that survive shape.

Every test that touches those rules runs under a controlled environment. Two of
them read `os.environ`, so a runner's own variables would otherwise decide
whether the suite passes, and the four runners do not have the same ones.
"""

import argparse
import json
import os
import pathlib
import stat
import subprocess
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import cli  # noqa: E402
from scriptorium.config import (  # noqa: E402
    DEFAULT_CONFIG,
    MISSING,
    PATH_VALUED_KEYS,
    ROUTING_STAGES,
    ConfigError,
    dump_json,
    resolve_route,
    route_entry,
    unset_in,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")

#: The whole point of the acceptance criterion: this string must not reach the
#: configuration file, the temporary file, stdout or stderr. Held once so every
#: assertion looks for the same bytes.
PASTED = "sk-REDACTED-LOOKING-VALUE"

#: A credential that satisfies `[A-Za-z_][A-Za-z0-9_]*` from end to end. Shape
#: cannot refuse it, which is why the length-and-case rule exists.
BARE_TOKEN = "hf_QRSTuvwxYZabcdefghijklmnopqrstuvw"


def _lx(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _env(**extra):
    """A minimal environment, because two of the `api_key_env` rules read `os.environ`.

    The content rule compares a value against everything exported, so a runner's
    own environment would decide the outcome. Only what an interpreter needs in
    order to start survives — on Windows, dropping `SystemRoot` or `PATH` means
    python does not launch at all — plus whatever the test exports on purpose.
    """
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    env = {name: value for name, value in os.environ.items() if name in keep}
    env["PYTHONPATH"] = SRC
    env.update(extra)
    return env


def _project(tmp_path, env=None):
    env = env if env is not None else _env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    return env


def _config(tmp_path):
    return json.loads((tmp_path / "lx.config.json").read_bytes().decode("utf-8"))


def _out(result):
    return result.stdout.decode("utf-8", "replace")


def _err(result):
    return result.stderr.decode("utf-8", "replace")


def _both(result):
    return _out(result) + _err(result)


# ── the resolver ───────────────────────────────────────────────────────────

def test_a_bare_string_entry_resolves_exactly_as_it_did_before_models_existed():
    """The compatibility promise, asserted against the shipped configuration.

    Every configuration in existence writes a routing value as a provider name,
    and `DEFAULT_CONFIG` still ships it that way. If the object form had become
    the only shape the resolver understood, every project on disk would have
    silently changed backend on upgrade.
    """
    expected = DEFAULT_CONFIG["providers"]["local"]["model"]
    for stage in ROUTING_STAGES:
        assert DEFAULT_CONFIG["routing"][stage] == "local", "the fixture, not the code"
        assert resolve_route(DEFAULT_CONFIG, stage) == ("local", expected)


def test_a_stage_with_no_entry_of_its_own_still_falls_back_to_draft():
    """Configurations in the wild name `draft` alone, and kept working before."""
    cfg = {**DEFAULT_CONFIG, "routing": {"draft": "openai"}}
    assert resolve_route(cfg, "polish")[0] == "openai"
    assert resolve_route(cfg, "repair")[0] == "openai"


def test_the_entrys_model_beats_the_providers_and_the_callers_beats_both():
    cfg = {**DEFAULT_CONFIG,
           "routing": {"draft": {"provider": "local", "model": "from-the-entry"}}}
    assert resolve_route(cfg, "draft") == ("local", "from-the-entry")
    assert resolve_route(cfg, "draft", model="typed") == ("local", "typed")
    assert resolve_route({**DEFAULT_CONFIG, "routing": {"draft": "local"}}, "draft") == (
        "local", DEFAULT_CONFIG["providers"]["local"]["model"])


def test_a_provider_override_drops_the_entrys_model_but_not_the_callers():
    """A model id belongs to the backend that serves it.

    `--provider openai` on a stage routed to a local Qwen build must not ask
    OpenAI for `qwen2.5:14b-instruct`. The caller's own `--model` survives,
    because that one was typed for this run and for this provider.
    """
    cfg = {**DEFAULT_CONFIG,
           "routing": {"draft": {"provider": "local", "model": "qwen2.5:14b-instruct"}}}
    assert resolve_route(cfg, "draft", provider="openai") == (
        "openai", DEFAULT_CONFIG["providers"]["openai"]["model"])
    assert resolve_route(cfg, "draft", provider="openai", model="gpt-4o") == (
        "openai", "gpt-4o")
    # Naming the same provider the entry named changes nothing.
    assert resolve_route(cfg, "draft", provider="local") == ("local", "qwen2.5:14b-instruct")


def test_a_malformed_entry_is_refused_rather_than_silently_rerouted():
    """The hazard is not a crash, it is a document arriving somewhere nobody chose.

    `routing.polish = ""` used to be falsy, fall through to `draft`, and send the
    polish pass to a backend the person had not selected — with no message. An
    absent key still falls back; a present and broken one is an error naming the
    stage.
    """
    for broken in ("", {}, {"model": "x"}, 7, None):
        cfg = {**DEFAULT_CONFIG, "routing": {"draft": "local", "polish": broken}}
        with pytest.raises(ConfigError) as e:
            resolve_route(cfg, "polish")
        assert "routing.polish" in str(e.value)
    # And the absent case is still the fallback, so the refusal is about
    # brokenness rather than about strictness.
    assert resolve_route({**DEFAULT_CONFIG, "routing": {"draft": "local"}}, "polish")[0] == "local"


def test_route_entry_reports_what_was_written_and_resolve_route_fills_it_in():
    """`lx routing show` needs both to tell an override from a default."""
    cfg = {**DEFAULT_CONFIG, "routing": {"draft": "local"}}
    assert route_entry(cfg, "draft") == ("local", "")
    assert resolve_route(cfg, "draft")[1] == DEFAULT_CONFIG["providers"]["local"]["model"]


def test_the_provider_is_built_with_the_resolved_model_and_the_config_is_not_touched():
    """One run's override may not change what the next run resolves."""
    from scriptorium.providers import build

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    provider = build("openai", cfg, "gpt-4o")
    assert provider.model == "gpt-4o"
    assert cfg["providers"]["openai"]["model"] == DEFAULT_CONFIG["providers"]["openai"]["model"]
    assert build("openai", cfg).model == DEFAULT_CONFIG["providers"]["openai"]["model"]


# ── lx routing ─────────────────────────────────────────────────────────────

def test_routing_set_writes_a_model_override_and_show_reports_it(tmp_path):
    env = _project(tmp_path)
    assert _lx(["routing", "set", "draft", "openai:gpt-4o-mini"], tmp_path, env).returncode == 0

    shown = _lx(["routing", "show"], tmp_path, env)
    assert shown.returncode == 0
    assert "draft → openai (gpt-4o-mini)" in _out(shown)
    assert _config(tmp_path)["routing"]["draft"] == {"provider": "openai",
                                                    "model": "gpt-4o-mini"}


def test_routing_set_without_a_model_keeps_the_bare_string(tmp_path):
    """The shape every existing configuration uses stays reachable.

    A writer that upgraded every entry to the object form would make one shape
    unwritable and turn a compatibility promise into a migration.
    """
    env = _project(tmp_path)
    assert _lx(["routing", "set", "polish", "claude"], tmp_path, env).returncode == 0
    assert _config(tmp_path)["routing"]["polish"] == "claude"


def test_only_the_first_colon_splits_a_model_id(tmp_path):
    """`qwen2.5:14b-instruct` is the shipped default, and it carries its own colon."""
    env = _project(tmp_path)
    assert _lx(["routing", "set", "draft", "local:qwen2.5:14b-instruct"],
               tmp_path, env).returncode == 0
    assert _config(tmp_path)["routing"]["draft"] == {"provider": "local",
                                                    "model": "qwen2.5:14b-instruct"}


def test_routing_set_refuses_an_unknown_provider_and_names_the_configured_ones(tmp_path):
    """Most of the value of the command: today a typo surfaces mid-run."""
    env = _project(tmp_path)
    before = (tmp_path / "lx.config.json").read_bytes()

    r = _lx(["routing", "set", "draft", "nosuchprovider"], tmp_path, env)
    assert r.returncode != 0
    message = _err(r)
    assert "Traceback" not in message
    for name in ("local", "lmstudio", "openai", "claude"):
        assert name in message
    assert (tmp_path / "lx.config.json").read_bytes() == before


def test_routing_set_refuses_a_stage_that_is_not_one_and_lists_the_stages(tmp_path):
    env = _project(tmp_path)
    r = _lx(["routing", "set", "review", "local"], tmp_path, env)
    assert r.returncode != 0
    message = _both(r)
    assert "Traceback" not in message
    for stage in ROUTING_STAGES:
        assert stage in message


def test_routing_show_reports_a_malformed_entry_instead_of_dying_on_it(tmp_path):
    """One broken stage must not take the other two down with it."""
    env = _project(tmp_path)
    config = _config(tmp_path)
    config["routing"]["polish"] = ""
    dump_json(str(tmp_path / "lx.config.json"), config)

    r = _lx(["routing", "show"], tmp_path, env)
    assert r.returncode == 0
    shown = _out(r)
    assert "routing.polish" in shown
    assert "draft → local" in shown and "repair → local" in shown


# ── lx config: the writer ──────────────────────────────────────────────────

def test_a_key_this_build_does_not_know_survives_a_write(tmp_path):
    """A configuration written by a newer version must survive an older one's write.

    The file is re-read raw and written back, so the writer never has an opinion
    about a key it has no schema for — which is also what keeps the file holding
    only what somebody chose rather than a materialized copy of every default.
    """
    env = _project(tmp_path)
    config = _config(tmp_path)
    config["a_key_from_a_later_build"] = {"keep": ["me", 2]}
    dump_json(str(tmp_path / "lx.config.json"), config)

    assert _lx(["config", "set", "batch.size", "10"], tmp_path, env).returncode == 0
    after = _config(tmp_path)
    assert after["a_key_from_a_later_build"] == {"keep": ["me", 2]}
    assert after["batch"]["size"] == 10, "written as a number, not as text"


def test_a_value_is_typed_from_what_the_key_already_holds(tmp_path):
    """`providers.openai.model 4` writes the string, because a model id is text."""
    env = _project(tmp_path)
    assert _lx(["config", "set", "providers.openai.model", "4"], tmp_path, env).returncode == 0
    assert _config(tmp_path)["providers"]["openai"]["model"] == "4"
    assert _lx(["config", "set", "targets", "zh-TW,ja"], tmp_path, env).returncode == 0
    assert _config(tmp_path)["targets"] == ["zh-TW", "ja"]


def test_unset_returns_a_key_to_its_default_and_is_quiet_when_there_is_nothing_to_do(tmp_path):
    env = _project(tmp_path)
    assert _lx(["config", "set", "batch.size", "3"], tmp_path, env).returncode == 0

    r = _lx(["config", "unset", "batch.size"], tmp_path, env)
    assert r.returncode == 0
    assert "batch" not in _config(tmp_path) or "size" not in _config(tmp_path)["batch"]
    assert str(DEFAULT_CONFIG["batch"]["size"]) in _out(r)

    again = _lx(["config", "unset", "batch.size"], tmp_path, env)
    assert again.returncode == 0, "unset is idempotent; the default already applies"


def test_unset_removes_a_block_it_emptied():
    """An empty block reads as a decision somebody made rather than the absence of one."""
    data = {"batch": {"size": 3}, "targets": ["zh-TW"]}
    assert unset_in(data, ["batch", "size"]) == 3
    assert data == {"targets": ["zh-TW"]}
    assert unset_in(data, ["batch", "size"]) is MISSING


def test_a_refused_value_leaves_the_file_byte_identical_and_no_temporary_behind(tmp_path):
    """Validation runs before the write, so a refusal costs nothing.

    The temporary file matters as much as the file: unguarded, an interrupted
    write left the whole configuration in a world-readable `lx.config.json.tmp`
    indefinitely.
    """
    env = _project(tmp_path)
    before = (tmp_path / "lx.config.json").read_bytes()

    for key, value in (("providers.local.timeout", "soon"),
                       ("providers.local.kind", "llama"),
                       ("providers.local.base_url", "ftp://example/v1"),
                       ("batch.size", "0")):
        r = _lx(["config", "set", key, value], tmp_path, env)
        assert r.returncode != 0, key
        assert "Traceback" not in _err(r), key
        assert value in _err(r) or key in _err(r), key

    assert (tmp_path / "lx.config.json").read_bytes() == before
    assert not (tmp_path / "lx.config.json.tmp").exists()


def test_an_unknown_backend_kind_names_the_ones_this_build_has(tmp_path):
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.local.kind", "llama"], tmp_path, env)
    assert r.returncode != 0
    for kind in ("openai", "anthropic"):
        assert kind in _err(r)


def test_a_value_where_a_block_is_expected_is_refused_rather_than_replacing_it(tmp_path):
    """`routing.draft.model` would otherwise throw away the provider name."""
    env = _project(tmp_path)
    r = _lx(["config", "set", "routing.draft.model", "gpt-4o"], tmp_path, env)
    assert r.returncode != 0
    assert "Traceback" not in _err(r)
    assert _config(tmp_path)["routing"]["draft"] == "local"


def test_the_temporary_file_is_created_with_the_mode_it_was_given(tmp_path, monkeypatch):
    """The mode reaches `os.open`, on every platform, in one call.

    A `chmod` after `open` leaves a window in which the bytes exist under
    whatever the umask decided. This asserts the code path everywhere; the test
    below asserts the result where the platform has modes to assert.
    """
    seen = []
    real_open = os.open

    def watched(path, flags, mode=0o777, **kw):
        seen.append((str(path), flags, mode))
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", watched)
    dump_json(str(tmp_path / "written.json"), {"a": 1}, create_mode=0o600)

    calls = [c for c in seen if c[0].endswith("written.json.tmp")]
    assert calls, "the temporary file was not opened through os.open"
    _, flags, mode = calls[-1]
    assert mode == 0o600
    assert flags & os.O_EXCL, "a planted link at the predictable name must be refused"


@pytest.mark.skipif(os.name != "posix", reason="Windows has no mode bits to assert")
def test_a_new_configuration_is_owner_only_and_a_chosen_mode_survives_a_rewrite(tmp_path):
    """Owner-only at *creation*; whatever the person chose afterwards is kept.

    `os.replace` gives the destination the temporary file's mode, so without the
    second half a configuration somebody deliberately made group-readable would
    silently become private on the first `lx config set`.
    """
    env = _project(tmp_path)
    path = tmp_path / "lx.config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    path.chmod(0o644)
    assert _lx(["config", "set", "batch.size", "9"], tmp_path, env).returncode == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


# ── lx config: credentials ─────────────────────────────────────────────────

def test_a_pasted_key_is_refused_and_no_part_of_it_is_ever_repeated(tmp_path):
    """The acceptance criterion, checked on every surface the value could reach.

    A refusal that quotes the value has published it to the terminal, to the
    scrollback, and to whatever the output was piped into — which is worse than
    the misconfiguration it was reporting.
    """
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.openai.api_key_env", PASTED], tmp_path, env)

    assert r.returncode != 0
    assert "Traceback" not in _err(r)
    for surface in (_out(r), _err(r),
                    (tmp_path / "lx.config.json").read_bytes().decode("utf-8")):
        assert PASTED not in surface
        assert "REDACTED-LOOKING" not in surface
    assert not (tmp_path / "lx.config.json.tmp").exists()
    # And the message still says what to do.
    assert "api_key_env" in _err(r) and "environment variable" in _err(r)


def test_a_token_that_is_a_legal_identifier_is_still_refused(tmp_path):
    """Shape alone is a sieve.

    `hf_…`, `ghp_…`, `github_pat_…`, `gsk_…` and every hex or base62 token that
    starts with a letter satisfy `[A-Za-z_][A-Za-z0-9_]*` from end to end. What
    refuses them is length together with case: a variable name is either short
    or upper-case by universal convention, and a twenty-character credential is
    upper-case with probability near zero.
    """
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.openai.api_key_env", BARE_TOKEN], tmp_path, env)
    assert r.returncode != 0
    assert BARE_TOKEN not in _both(r)
    assert BARE_TOKEN not in (tmp_path / "lx.config.json").read_bytes().decode("utf-8")


def test_a_long_upper_case_name_is_accepted_because_that_is_what_names_look_like(tmp_path):
    env = _project(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "HUGGING_FACE_HUB_TOKEN"):
        r = _lx(["config", "set", "providers.claude.api_key_env", name], tmp_path, env)
        assert r.returncode == 0, _err(r)
        assert _config(tmp_path)["providers"]["claude"]["api_key_env"] == name


def test_a_variable_that_is_already_set_is_always_accepted_as_a_name(tmp_path):
    """The documented escape from the two heuristics above.

    A legitimate long lower-case name only has to exist in the environment
    first, and both refusals say so.
    """
    lower = "my_project_translation_token"
    assert len(lower) >= 20 and any(c.islower() for c in lower), "the fixture, not the code"
    env = _project(tmp_path, _env(**{lower: "anything"}))
    r = _lx(["config", "set", "providers.openai.api_key_env", lower], tmp_path, env)
    assert r.returncode == 0, _err(r)
    assert _config(tmp_path)["providers"]["openai"]["api_key_env"] == lower


def test_the_content_of_a_set_variable_is_refused_and_only_its_name_is_printed(tmp_path):
    """The rule that catches a short or upper-case token the length rule lets by.

    A name is not a secret, so the message may say which variable held it —
    which is also the whole of the fix.
    """
    secret = "S3CRETVALUE"
    env = _project(tmp_path, _env(MY_BACKEND_KEY=secret))
    r = _lx(["config", "set", "providers.openai.api_key_env", secret], tmp_path, env)
    assert r.returncode != 0
    # Every eight-character window, not the whole string: this is the one
    # refusal holding a real secret, and `held[:8]` passed `secret not in` —
    # measured by the security-tier re-derivation of 2026-09-13.
    assert _windows(secret, _both(r)) == [], _both(r)
    assert "MY_BACKEND_KEY" in _err(r)


def test_a_trailing_newline_does_not_get_a_name_past_the_shape_rule(monkeypatch):
    """`$` matches before a trailing newline; `fullmatch` is why this is not a hole.

    A trailing newline is exactly what a clipboard carries, so `^…$` with
    `re.match` would have accepted `"OPENAI_API_KEY\\n"` — and then every later
    lookup of a variable by that name would fail for a reason nobody could see.
    Asserted on the rule rather than through argv, because a newline inside a
    command-line argument is quoted differently by every shell in the matrix.
    """
    monkeypatch.setattr(os, "environ", {})
    with pytest.raises(ConfigError):
        cli._field_api_key_env(DEFAULT_CONFIG, "providers.openai.api_key_env",
                               "OPENAI_API_KEY\n")
    assert cli._field_api_key_env(
        DEFAULT_CONFIG, "providers.openai.api_key_env", "OPENAI_API_KEY") == "OPENAI_API_KEY"


def test_the_empty_string_stays_a_valid_api_key_env(tmp_path):
    """It is the shipped default for a local runtime and means `no key needed`."""
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.local.api_key_env", ""], tmp_path, env)
    assert r.returncode == 0, _err(r)
    assert _config(tmp_path)["providers"]["local"]["api_key_env"] == ""


def test_a_json_block_cannot_smuggle_a_value_past_the_rule_that_owns_it(tmp_path):
    """A check keyed on the typed key alone would be decoration.

    `lx config set providers.openai '{"api_key_env": …}'` writes the same leaf as
    the dotted spelling does. The rules are applied where a field *lands*, not by
    how it was addressed — the rule `web/server.py` already follows for `src`.
    """
    env = _project(tmp_path)
    payload = json.dumps({"kind": "openai", "base_url": "https://x.example/v1",
                          "api_key_env": PASTED})

    r = _lx(["config", "set", "providers.smuggled", payload], tmp_path, env)
    assert r.returncode != 0
    assert PASTED not in _both(r)
    assert "smuggled" not in (tmp_path / "lx.config.json").read_bytes().decode("utf-8")


def test_a_header_is_not_writable_from_the_command_line(tmp_path):
    """`providers.*.headers` goes onto the wire verbatim, in a file meant to be committed.

    Refused at the block and at anything inside it, so naming the header rather
    than the block does not walk around the rule. The non-secret uses stay
    reachable by hand-editing, which is where they are today.
    """
    env = _project(tmp_path)
    for key in ("providers.local.headers", "providers.local.headers.Authorization"):
        r = _lx(["config", "set", key, '{"Authorization": "Bearer x"}'], tmp_path, env)
        assert r.returncode != 0, key
        assert "api_key_env" in _err(r), key
    r = _lx(["config", "set", "providers.local",
             json.dumps({"headers": {"Authorization": "Bearer x"}})], tmp_path, env)
    assert r.returncode != 0, "a block write must not reach a header either"
    assert "headers" not in json.dumps(_config(tmp_path))


def test_a_base_url_carrying_a_credential_is_refused_without_echoing_it(tmp_path):
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.openai.base_url",
             f"https://user:{PASTED}@gw.example/v1"], tmp_path, env)
    assert r.returncode != 0
    assert PASTED not in _both(r)
    assert "api_key_env" in _err(r)


def test_get_reports_whether_the_variable_is_set_and_never_what_it_holds(tmp_path):
    secret = "the-value-behind-the-name"
    env = _project(tmp_path, _env(OPENAI_API_KEY=secret))
    r = _lx(["config", "get", "providers.openai.api_key_env"], tmp_path, env)
    assert r.returncode == 0
    assert "OPENAI_API_KEY" in _out(r) and "set" in _out(r)
    assert secret not in _out(r)

    absent = _lx(["config", "get", "providers.openai.api_key_env"], tmp_path, _env())
    assert "not set" in _out(absent)


def test_get_masks_what_a_hand_edited_file_may_hold(tmp_path):
    """The writer refuses these; the file is editable by hand forever.

    `lx config get` is the command a person runs when something is wrong, so it
    is the command most likely to be pasted into an issue.
    """
    env = _project(tmp_path)
    config = _config(tmp_path)
    config["providers"]["claude"].update({
        "api_key_env": PASTED,
        "headers": {"Authorization": f"Bearer {PASTED}"},
        "base_url": f"https://user:{PASTED}@gw.example/v1?key={PASTED}",
    })
    dump_json(str(tmp_path / "lx.config.json"), config)

    for args in (["config", "get"], ["config", "get", "providers"],
                 ["config", "get", "providers.claude"],
                 ["config", "get", "providers.claude.api_key_env"],
                 ["config", "get", "providers.claude.headers"],
                 ["config", "get", "providers.claude.headers.Authorization"],
                 ["config", "get", "providers.claude.base_url"]):
        r = _lx(args, tmp_path, env)
        assert r.returncode == 0, _err(r)
        assert PASTED not in _out(r), args
    # The host survives, because "where is my document going" is the question.
    assert "gw.example" in _out(_lx(["config", "get", "providers.claude.base_url"],
                                    tmp_path, env))


def test_no_lx_command_takes_key_material_on_a_command_line(tmp_path):
    """argv is in a process listing and in shell history before any refusal runs.

    Nothing here can un-leak a mistaken paste, so the only real countermeasure is
    that no command ever asks for one. This pins the promise: the `set` command's
    own help says so, and it is the place somebody looks.
    """
    env = _project(tmp_path)
    r = _lx(["config", "set", "--help"], tmp_path, env)
    assert r.returncode == 0
    assert "NAME of an environment variable" in _out(r)


# ── what the adversarial pass found, and what keeps it closed ──────────────
#
# Every test below pins a defect the review of the first draft reproduced. They
# are grouped because they share one lesson: a rule that fires on the key
# somebody typed is not a rule, and the two commands over one value have to
# agree about what may be printed.

def test_a_key_may_not_be_addressed_inside_something_that_holds_one_value(tmp_path):
    """The hole `_WHOLE_BLOCK` closed for headers and left open everywhere else.

    `lx config set providers.new.api_key_env.x sk_live_…` exited 0 and wrote the
    credential into the committed file: the rule matched the three-segment
    prefix, the path had four, and nothing fired. The four providers `lx init`
    scaffolds were incidentally safe — their `api_key_env` is already a string in
    the raw file — and a backend somebody adds was not, which is the case this
    command exists for.
    """
    env = _project(tmp_path)
    assert _lx(["config", "set", "providers.myproxy.kind", "openai"],
               tmp_path, env).returncode == 0

    r = _lx(["config", "set", "providers.myproxy.api_key_env.x", "sk_live_deadbeef"],
            tmp_path, env)
    assert r.returncode != 0
    assert "sk_live_deadbeef" not in _both(r)
    assert "sk_live_deadbeef" not in (tmp_path / "lx.config.json").read_bytes().decode("utf-8")


def test_the_merged_configurations_own_type_refuses_a_path_into_a_scalar(tmp_path):
    """`set_in` sees the raw file, and the raw file usually does not hold the key.

    So the guard that refuses to descend into a value could not see that
    `batch.size` is a number: on a fresh project `lx config set batch.size.x 1`
    wrote `{"batch": {"size": {"x": 1}}}` and the next `lx translate` died inside
    `_chunks`. Every shape below was reproduced.
    """
    env = _project(tmp_path)
    before = (tmp_path / "lx.config.json").read_bytes()
    for key in ("batch.size.x", "providers.local.model.name",
                "providers.local.timeout.secs", "length_ratio.zh-TW.min",
                "targets.0", "source_lang.x", "output_pattern.dir"):
        r = _lx(["config", "set", key, "1"], tmp_path, env)
        assert r.returncode != 0, key
        assert "Traceback" not in _err(r), key
        assert key.rsplit(".", 1)[0] in _err(r), key
    assert (tmp_path / "lx.config.json").read_bytes() == before


def test_addressing_inside_a_routing_entry_says_which_key_is_the_problem(tmp_path):
    """`draft` IS a stage; the message used to claim it was not.

    The stage check saw `draft.model` as the stage name because the rule owned
    the whole block. Naming the real problem — a routing entry holds one value —
    is what tells somebody what to type next.
    """
    env = _project(tmp_path)
    r = _lx(["config", "set", "routing.draft.model", "gpt-4o"], tmp_path, env)
    assert r.returncode != 0
    assert "routing.draft" in _err(r)
    assert "is not a pipeline stage" not in _err(r)
    assert "lx routing set draft" in _err(r)


def test_nan_and_inf_are_refused_like_any_other_non_number(tmp_path):
    """Every comparison against a nan is False, so no window rejects one.

    `int(float("nan"))` then raises the interpreter's own ValueError, which is
    not a `ConfigError` — so `lx config set batch.size nan` produced a traceback
    and exit 1 where every other refused value gets one sentence and exit 2.
    `1e400` is `inf` through the same door.
    """
    env = _project(tmp_path)
    for key, value in (("batch.size", "nan"), ("batch.size", "inf"),
                       ("providers.local.timeout", "1e400"),
                       ("providers.local.temperature", "nan")):
        r = _lx(["config", "set", key, value], tmp_path, env)
        assert r.returncode == 2, (key, value)
        assert "Traceback" not in _err(r), (key, value)


def test_no_base_url_refusal_echoes_the_value(tmp_path):
    """This field sits directly above `api_key_env`; it is where a key gets pasted.

    The not-a-URL branch interpolated the rejected value, so pasting a key into
    the wrong field printed it to stderr — the one thing the whole credential
    rule set exists to prevent, reached through the neighbouring field.
    """
    env = _project(tmp_path)
    for value in (PASTED, BARE_TOKEN, "ftp://example/v1", "not a url at all"):
        r = _lx(["config", "set", "providers.openai.base_url", value], tmp_path, env)
        assert r.returncode != 0, value
        assert value not in _both(r), value
        assert "http://localhost:11434/v1" in _err(r), "the message still says the shape"


def test_a_query_string_in_a_base_url_is_refused(tmp_path):
    """A credential reaches a URL two ways, and only one of them was refused.

    `https://gw.example/v1?key=…` was written to the file, which made the README
    claim that `lx config set` will not put a credential there simply false.
    Every query is refused rather than a guessed-at list of parameter names —
    invariant 4 — and the escape for a genuine one is hand-editing, as it is for
    a header.
    """
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.openai.base_url",
             f"https://gw.example.com/v1?key={PASTED}"], tmp_path, env)
    assert r.returncode != 0
    assert PASTED not in _both(r)
    assert PASTED not in (tmp_path / "lx.config.json").read_bytes().decode("utf-8")
    # The plain endpoint is still writable.
    assert _lx(["config", "set", "providers.openai.base_url", "https://gw.example.com/v1"],
               tmp_path, env).returncode == 0


def test_lx_providers_masks_what_lx_config_get_masks(tmp_path):
    """Two commands over one value must not disagree about what is printable.

    `lx providers` printed a hand-edited `?key=…` in full while `lx config get`
    was masking it, and `/api/state` served the same unmasked string to the
    browser. One function, `config.printable_url`, now answers for all three.
    """
    env = _project(tmp_path)
    config = _config(tmp_path)
    config["providers"]["openai"]["base_url"] = f"https://gw.example.com/v1?key={PASTED}"
    dump_json(str(tmp_path / "lx.config.json"), config)

    listed = _out(_lx(["providers"], tmp_path, env))
    assert PASTED not in listed
    assert "gw.example.com" in listed

    from scriptorium.providers import available
    assert PASTED not in json.dumps(available(config))


def test_a_failure_that_is_not_an_oserror_still_removes_the_temporary_file(tmp_path,
                                                                          monkeypatch):
    """"The temporary file never survives a failure" was true of OSError only.

    `json.load` accepts a lone surrogate escape, and the write back then dies at
    the encode — not an `OSError`, so the whole configuration was left in a
    world-readable `.tmp`. A Ctrl-C between the write and the replace is the same
    hole.
    """
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "written.json"

    class _Boom(Exception):
        pass

    real_replace = os.replace

    def exploding(src, dst, *a, **kw):
        if str(dst).endswith("written.json"):
            raise _Boom("interrupted between the write and the replace")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", exploding)
    with pytest.raises(_Boom):
        dump_json(str(target), {"a": 1}, create_mode=0o600)
    assert not (tmp_path / "written.json.tmp").exists()
    assert not target.exists()


def test_a_config_that_cannot_be_written_back_is_refused_in_one_sentence(tmp_path):
    """Readable and unwritable is a real state, and it used to end in a traceback."""
    env = _project(tmp_path)
    raw = (tmp_path / "lx.config.json").read_bytes().decode("utf-8")
    surrogate = chr(92) + "ud800"          # the six characters, not the character
    (tmp_path / "lx.config.json").write_bytes(
        raw.replace('"targets"', f'"note": "{surrogate}", "targets"', 1).encode("utf-8"))

    r = _lx(["config", "set", "batch.size", "5"], tmp_path, env)
    assert r.returncode == 2
    assert "Traceback" not in _err(r)
    assert "unchanged" in _err(r)
    assert not (tmp_path / "lx.config.json.tmp").exists()


# ── what the other surfaces see ────────────────────────────────────────────

def test_the_dry_run_line_names_the_provider_and_the_model_it_would_use(tmp_path):
    env = _project(tmp_path)
    (tmp_path / "d.md").write_bytes(b"One sentence here.\n")
    assert _lx(["routing", "set", "draft", "openai:gpt-4o-mini"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    routed = _lx(["translate", "d.md", "--lang", "zh-TW", "--dry-run"], tmp_path, env)
    assert "provider=openai" in _out(routed) and "model=gpt-4o-mini" in _out(routed)

    typed = _lx(["translate", "d.md", "--lang", "zh-TW", "--dry-run", "--model", "mine"],
                tmp_path, env)
    assert "model=mine" in _out(typed)

    # A different backend drops the entry's model rather than carrying it across.
    other = _lx(["translate", "d.md", "--lang", "zh-TW", "--dry-run", "--provider", "claude"],
                tmp_path, env)
    assert "provider=claude" in _out(other)
    assert DEFAULT_CONFIG["providers"]["claude"]["model"] in _out(other)


def test_providers_prints_routing_in_the_spelling_routing_set_takes_back(tmp_path):
    env = _project(tmp_path)
    assert _lx(["routing", "set", "draft", "openai:gpt-4o-mini"], tmp_path, env).returncode == 0
    listed = _out(_lx(["providers"], tmp_path, env))
    assert "draft=openai:gpt-4o-mini" in listed
    assert "polish=local" in listed


def test_the_workbench_state_reports_routing_resolved(tmp_path, monkeypatch):
    """A page that read the configured value would break on the object form.

    Assigning `{"provider": …}` to a `<select>`'s value yields `[object Object]`,
    the control shows nothing, and the run goes to whichever backend sorted
    first. `/api/state` therefore projects one shape, resolved through the same
    function the CLI prints from.
    """
    from scriptorium.web.server import _routing_state

    cfg = {**DEFAULT_CONFIG,
           "routing": {"draft": {"provider": "openai", "model": "gpt-4o-mini"},
                       "polish": "local", "repair": ""}}
    state = _routing_state(cfg)
    assert state["draft"] == {"provider": "openai", "model": "gpt-4o-mini"}
    assert state["polish"] == {"provider": "local",
                               "model": DEFAULT_CONFIG["providers"]["local"]["model"]}
    assert "error" in state["repair"], "a broken stage is reported, not raised"
    assert set(state) == set(ROUTING_STAGES)


def test_every_stage_that_can_be_routed_is_a_mode_translate_accepts():
    """One tuple, or a stage silently stops being routable.

    `--mode`'s choices, `DEFAULT_CONFIG["routing"]` and `lx routing set`'s stage
    argument all read `ROUTING_STAGES`; this is the assertion that they still do.
    """
    parser = cli.build_parser()
    modes = None
    for action in parser._subparsers._group_actions[0].choices["translate"]._actions:
        if action.dest == "mode":
            modes = tuple(action.choices)
    assert modes == ROUTING_STAGES
    assert tuple(DEFAULT_CONFIG["routing"]) == ROUTING_STAGES


def test_the_config_command_is_reachable_through_the_parser(tmp_path):
    """`do_` functions are the API; the `cmd_` handlers stay thin above them."""
    args = cli.build_parser().parse_args(["config", "set", "batch.size", "5"])
    assert args.fn is cli.cmd_config_set
    assert (args.key, args.value) == ("batch.size", "5")
    assert cli.build_parser().parse_args(["routing", "show"]).fn is cli.cmd_routing_show
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["config"])


def test_do_config_set_reports_the_previous_value_and_writes_the_new_one(tmp_path,
                                                                        monkeypatch):
    """The library half, because the eventual settings surface calls this and not argv."""
    monkeypatch.chdir(tmp_path)
    dump_json("lx.config.json", {"batch": {"size": 4}})
    cfg = {**DEFAULT_CONFIG, "batch": {**DEFAULT_CONFIG["batch"], "size": 4}}

    assert cli.do_config_set(cfg, "batch.size", "12") == (4, 12)
    assert cli.do_config_set(cfg, "batch.concurrency", "3")[0] is MISSING
    assert json.loads(pathlib.Path("lx.config.json").read_bytes().decode("utf-8")) == {
        "batch": {"size": 12, "concurrency": 3}}


def test_an_argparse_namespace_from_the_parser_carries_every_flag_translate_reads():
    """`_run_translate` reads `args.model`; a subcommand that forgot it would crash.

    Asserted for all three model-calling commands rather than for one, because
    `_add_llm_flags` is applied per subparser and the omission is invisible until
    somebody runs that command.
    """
    parser = cli.build_parser()
    for command in ("translate", "repair", "run"):
        args = parser.parse_args([command, "d.md", "--lang", "zh-TW"])
        for flag in ("provider", "model", "batch", "concurrency", "dry_run"):
            assert hasattr(args, flag), f"{command} is missing --{flag}"
        assert isinstance(args, argparse.Namespace)


# ── the local runtimes a fresh project is offered ──────────────────────────

def test_a_fresh_project_is_scaffolded_with_a_llamacpp_entry(tmp_path):
    """`lx init` offers llama.cpp beside Ollama and LM Studio.

    Membership, not an exact set: `DEFAULT_CONFIG` gaining an entry is additive
    — `config.load_config` layers a user's file over it, so an existing project
    gains the entry without losing anything — and a test that pinned the whole
    set would turn every future addition into a red suite for no defect.
    """
    env = _project(tmp_path)
    written = _config(tmp_path)["providers"]
    for name in ("local", "lmstudio", "llamacpp", "openai", "claude"):
        assert name in written, f"{name} is not scaffolded"

    entry = written["llamacpp"]
    assert entry["kind"] == "openai", "llama.cpp is OpenAI-compatible; not a third kind"
    assert entry["api_key_env"] == "", "a local runtime wants no Authorization header"
    # 8080 is llama.cpp's own documented default, not the port any one machine
    # happens to use — the two entries above it ship Ollama's 11434 and LM
    # Studio's 1234 for the same reason.
    assert entry["base_url"] == "http://localhost:8080/v1"
    # Longer than the 300 the other two local runtimes get, and the number comes
    # from a measurement: `llama-server` in router mode autoloads on demand and
    # **blocks the caller** for the whole load rather than answering a retryable
    # status, so `timeout` is the only knob that helps. See `config.py`.
    assert entry["timeout"] > DEFAULT_CONFIG["providers"]["lmstudio"]["timeout"]

    result = _lx(["config", "get", "providers.llamacpp.base_url"], tmp_path, env)
    assert result.returncode == 0
    assert _out(result).strip() == "http://localhost:8080/v1"


def test_the_default_routing_still_names_local_after_llamacpp_was_added():
    """Adding a provider must not repoint anybody's stages.

    Separated from the membership test above because the two fail for different
    reasons: this one is the compatibility promise, and it is the assertion a
    well-meant "make llama.cpp the default" would break.
    """
    for stage in ROUTING_STAGES:
        assert DEFAULT_CONFIG["routing"][stage] == "local"


# ── the version segment: said, never enforced ──────────────────────────────

@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "http://localhost:11434/v1",
    "http://localhost:8080/v1/",
    "https://host/api/v1",              # a proxy behind a prefix of its own
    "https://res.openai.azure.com/openai/v1/",
    "https://host/v1beta",              # Google's spelling
    "https://host/v1beta2",             # and its numbered variant
    "https://host/v1alpha1",
    "https://host/v2",                  # Continue.dev #7682's endpoint
    "https://host/v10/chat",
    "https://host/V1",                  # case: a path is not lowercased for us
])
def test_an_ordinary_versioned_endpoint_has_a_version_segment(url):
    assert cli.has_version_segment(url) is True


@pytest.mark.parametrize("url", [
    "http://127.0.0.1",                # a bare host: llama.cpp serves chat here, measured
    "http://127.0.0.1/",
    "https://host/openai",
    # The next four all contain the literal text `/v1/` or `/v1` somewhere in the
    # URL and none of them in the *path*. They are here because a mutation that
    # searched the whole URL instead of `urlsplit(url).path` survived the rest of
    # this list: `https://v1.example.com/` does not distinguish the two, since the
    # `v1` there is followed by a dot and matches neither way.
    "https://v1/",                     # the host is literally `v1`
    "https://v1.example.com/",         # the version is in the HOST, not the path
    "https://host/?p=/v1/",            # in the query
    "https://host/#/v1/",              # in the fragment
    "https://host/?version=v1",
    "https://host/vone",
    "https://host/service/v8080x",     # a version-looking run that is not a segment
    "https://host/inv1",
    # `\\d` is Unicode-aware in Python, so these three silenced the note until the
    # pattern was narrowed to `[0-9]`. An endpoint served under an Arabic-Indic
    # or fullwidth "1" is not a thing; a regex that accepts one is.
    "http://host/v١",             # Arabic-Indic one
    "http://host/v１",             # fullwidth one
    "http://host/v۱",             # extended Arabic-Indic one
    "",
    None,
    b"http://host",                    # not a string at all
    12345,
])
def test_a_path_without_a_version_segment_is_reported_as_such(url):
    """And each of these is still a legal thing to configure.

    The bare host at the top is the shape that decided the whole
    question: measured on 2026-08-20, llama.cpp serves chat completions there and
    at `/v1` alike, so a rule that refused it or repaired it would have broken a
    working configuration. The helper only ever *reports*.
    """
    assert cli.has_version_segment(url) is False


def test_a_base_url_with_no_version_segment_is_written_and_noted(tmp_path):
    """Noted, and **written** — the note is not a refusal wearing a hat.

    Deleting the `has_version_segment` branch in `cmd_config_set` makes the
    second assertion fail; turning the note into a `ConfigError` makes the first
    and third fail. Both mutations were run.
    """
    env = _project(tmp_path)
    result = _lx(["config", "set", "providers.llamacpp.base_url",
                  "http://127.0.0.1"], tmp_path, env)
    assert result.returncode == 0, "a note never blocks the write"
    assert "version segment" in _out(result)
    assert _config(tmp_path)["providers"]["llamacpp"]["base_url"] == "http://127.0.0.1"
    # It points at the command that can actually answer the question, rather
    # than asserting what the path should have been.
    assert "lx models --provider llamacpp" in _out(result)


def test_a_versioned_base_url_is_written_without_a_note(tmp_path):
    env = _project(tmp_path)
    for url in ("http://127.0.0.1/v1", "https://host/api/v1", "https://host/v2"):
        result = _lx(["config", "set", "providers.llamacpp.base_url", url], tmp_path, env)
        assert result.returncode == 0
        assert "version segment" not in _out(result), f"{url} was libelled"


def test_the_note_reaches_a_base_url_written_inside_a_block(tmp_path):
    """A rule is about where a field lands, and so is a note about that field.

    The advisory lines were keyed on the last segment of the key, so the leaf
    spelling printed them and the block spelling — which is what the README's own
    worked example uses — printed nothing. Measured 2026-08-20.
    """
    env = _project(tmp_path)
    block = json.dumps({"kind": "openai", "base_url": "http://127.0.0.1",
                        "model": "m", "api_key_env": ""})
    result = _lx(["config", "set", "providers.router", block], tmp_path, env)
    assert result.returncode == 0
    assert "version segment" in _out(result)
    assert "providers.router.base_url is where the document" in _out(result)


def test_the_note_addresses_a_provider_whose_name_contains_a_dot(tmp_path):
    """A provider name is a key, and a key may hold a dot; a joined path is not its address.

    `lx config set providers '{"a.b": {…}}'` writes one, and splitting the joined
    dotted key back apart pointed the advice at `--provider a`, which does not
    exist. Found by the adversarial pass, 2026-08-20.

    **The endpoint is a dead end, and the listing asks once.** This runs in a
    child process, which `tests/conftest.py`'s guard cannot see, and with a bare
    `http://127.0.0.1` the child dialled port 80 twice — on a machine serving
    anything there, the request reached it. Measured 2026-09-13 (HANDOFF-082).
    Port 9 carries no version segment either, so the note under test still fires.
    """
    env = _project(tmp_path)
    block = json.dumps({"a.b": {"kind": "openai", "base_url": "http://127.0.0.1:9",
                                "model": "m", "api_key_env": "", "retries": 0}})
    result = _lx(["config", "set", "providers", block], tmp_path, env)
    assert result.returncode == 0
    assert "lx models --provider a.b" in _out(result)
    assert "--provider a`" not in _out(result)
    # And the name it printed is one `lx models` actually resolves.
    listing = _lx(["models", "--provider", "a.b"], tmp_path, env)
    assert "unknown provider" not in _both(listing)
    # The absence above passes for any refusal at all, including one made before
    # the name was looked up. This sentence is written by a provider built under
    # `a.b`, after it asked the transport — so the lookup happened, and the
    # request went to the dead end and nowhere else.
    assert "a.b: cannot reach http://127.0.0.1:9/models" in _err(listing), _both(listing)


def test_a_note_never_appears_where_no_base_url_landed(tmp_path):
    env = _project(tmp_path)
    for key, value in (("batch.size", "10"),
                       ("providers.llamacpp.model", "some/model:Q4"),
                       ("providers.llamacpp.timeout", "900")):
        result = _lx(["config", "set", key, value], tmp_path, env)
        assert result.returncode == 0
        assert "version segment" not in _out(result)
        assert "where the document under translation is sent" not in _out(result)


def test_a_router_model_id_round_trips_byte_identically(tmp_path):
    """A slash, a dot and a colon in a model id, through `set` and back out of `get`.

    The dot is the one under suspicion: dotted-key addressing is the mechanism
    in this project that could plausibly read it as structure. Here it is a
    *value*, and `split_key` never sees it — but "should" is not a test, and a
    llama.cpp router serves nothing but ids of this shape.
    """
    env = _project(tmp_path)
    for model in ("unsloth/Qwen3.6-35B-A3B-GGUF:IQ2_M",
                  "mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M:IMMERSIVETRANSLATE",
                  "ScrambieBambie_Snowpiercer-15B-v2_Q8_0"):
        assert _lx(["config", "set", "providers.llamacpp.model", model],
                   tmp_path, env).returncode == 0
        result = _lx(["config", "get", "providers.llamacpp.model"], tmp_path, env)
        assert result.returncode == 0
        assert _out(result).strip() == model, "the id came back changed"
        assert _config(tmp_path)["providers"]["llamacpp"]["model"] == model


# ── what an untrusted caller may write ─────────────────────────────────────
#
# `cli.writable_key` is the gate `POST /api/config` stands behind. It is tested
# here rather than in `test_web.py` because it is a `cli.py` rule — the shape
# `confined_path` and `language_tag` already have — and because the property that
# makes it sufficient is a property of the field table, not of the wire.


def test_the_http_allowlist_holds_only_keys_the_field_table_decides_by_itself():
    """The structural property the endpoint's safety rests on, in one assertion.

    `config_value` short-circuits the moment a field rule fires, so `_decode`'s
    type guessing and `_validated`'s descent into a block are both unreachable
    for a key that has its own rule. That is what makes a value unable to land
    anywhere except the key that was addressed — and it stops being true the day
    a key with no rule joins the list.

    A subset, deliberately not equality. Equality would make the list *derived*
    in all but spelling, so a field added to `_CONFIG_FIELDS` for an unrelated
    reason — the `cert_path` that `config.PATH_VALUED_KEYS`' own comment predicts
    as "a fifth path key added anywhere else" — would become writable over HTTP
    the same afternoon with nobody deciding it. This way that field is writable
    from a terminal and reaching the wire takes an edit here.
    """
    decided_alone = set(cli._CONFIG_FIELDS) - cli._WHOLE_BLOCK
    surplus = set(cli.HTTP_WRITABLE_KEYS) - decided_alone
    assert not surplus, (
        f"{sorted(surplus)} are writable over HTTP and are not keys the field table "
        f"decides on its own. A key with no rule of its own reaches `_validated`, "
        f"which descends into a block — the whole class this gate closes.")
    for pattern in cli.HTTP_WRITABLE_KEYS:
        parts = ["mine" if part == "*" else part for part in pattern.split(".")]
        found, rule = cli._exact_rule(parts)
        assert rule is not None and found == pattern, (
            f"{pattern} is on the allowlist and `_exact_rule` answers {found!r} for it")


@pytest.mark.parametrize("pattern",
                         [p for p in cli.HTTP_WRITABLE_KEYS if p != "routing.*"])
def test_no_admitted_key_accepts_a_block_as_its_value(pattern):
    """The other half of that property, from the value's side.

    `routing.*` is excluded because it is the one rule that legitimately takes an
    object — and the test below covers what it does with one.
    """
    with pytest.raises(ConfigError):
        cli.config_value(DEFAULT_CONFIG, pattern.replace("*", "mine"), {"a": 1})


def test_a_routing_object_is_rebuilt_rather_than_stored():
    """The one admitted key that takes an object reads two fields and writes two.

    So a block cannot ride in under it either: anything else in the object is
    dropped, and an object naming no model comes back as the bare string every
    configuration on disk uses — which is what keeps a screen that always emits
    the object form from migrating the file.
    """
    _, value = cli.config_value(
        DEFAULT_CONFIG, "routing.draft",
        {"provider": "local", "model": "m", "headers": {"Authorization": "Bearer x"}})
    assert value == {"provider": "local", "model": "m"}
    _, bare = cli.config_value(DEFAULT_CONFIG, "routing.draft", {"provider": "local"})
    assert bare == "local"


@pytest.mark.parametrize("key", [
    "glossary", "dnt", "style", "output_pattern", "sources",
    "providers.x.headers", "providers.x.headers.Authorization",
    "providers", "providers.openai", "routing", "routing.draft.model",
    "batch", "batch.size.x", "targets", "tone", "formats.map", "lexicon_extra",
    "embedding", "embedding.provider", "embedding.provider.x",
])
def test_a_key_off_the_http_allowlist_is_refused_before_its_value_is_looked_at(key):
    """One case per key, not one per class, because the classes differ.

    `output_pattern` is a file write outside the project through `/api/render`'s
    default output; `providers.openai` is the block spelling an allowlist keyed
    on what somebody typed would miss; `batch.size.x` is one of the two bypasses
    measured on 2026-08-12. They are refused by the same mechanism for different
    reasons, and a list is what keeps a future widening from dropping one.

    `embedding.provider` is the newest and the reason the list is a list rather
    than a property. It is a key the field table decides by itself, so the
    structural test above it is satisfied by admitting it; it holds no path, so
    the `PATH_VALUED_KEYS` test does not reach it. Nothing but this line stops
    the one-line change that would let a cross-site-reachable endpoint repoint
    the host every source and every translation in the project is POSTed to.
    `tone` joined it on 2026-09-11: it got a rule of its own, which satisfies the
    structural test by the same arithmetic, and it is on the list for that reason.
    """
    with pytest.raises(cli.UnwritableKey):
        cli.writable_key(key)


def test_no_path_valued_key_can_ever_reach_the_http_allowlist():
    """Stated over the constant rather than over a list of today's four.

    `config.PATH_VALUED_KEYS` exists to be added to, and its own comment says the
    fifth entry is the one confinement misses. This fails the day one is added
    and admitted, which is the only moment anybody could notice.
    """
    for key in PATH_VALUED_KEYS + ("sources",):
        assert not any(cli._pattern_matches(pattern, [key])
                       for pattern in cli.HTTP_WRITABLE_KEYS), f"{key} became writable"


def test_the_gate_never_sees_the_value_it_refuses():
    """A refusal that quoted the value would publish a mispasted credential.

    The strongest form of that promise is arithmetic rather than careful wording:
    membership is decided from the key alone, so `writable_key` is not given a
    value at all and has nothing to leak.
    """
    import inspect
    assert list(inspect.signature(cli.writable_key).parameters) == [
        "key", "confirm_base_url"]
    with pytest.raises(cli.UnwritableKey) as caught:
        cli.writable_key("providers.x.headers.Authorization")
    assert PASTED not in str(caught.value)


@pytest.mark.parametrize("confirm", [False, None, "true", 1])
def test_a_base_url_is_not_writable_without_the_acknowledgement(confirm):
    """And the acknowledgement is the JSON boolean, not anything truthy.

    `confirm_base_url is not True` rather than a truthiness test, because the
    string "true" arrives from a form body and would otherwise satisfy a guard
    whose failure direction is a credential going somewhere nobody chose.
    """
    with pytest.raises(ConfigError) as caught:
        cli.writable_key("providers.x.base_url", confirm_base_url=confirm)
    assert "confirm_base_url" in str(caught.value)
    assert cli.writable_key("providers.x.base_url", confirm_base_url=True) == [
        "providers", "x", "base_url"]


def test_the_two_refusals_are_different_classes_because_the_statuses_differ():
    """403 says "never writable"; 400 says "fix the payload and send it again".

    The contract forbids a client reading the sentence, so if both answered 403 a
    settings screen could not tell asking the person from giving up.
    `UnwritableKey` is also deliberately not an `UnsafePath`: that one names a
    *path*, and its `{field} = {value!r}` convention repeats what it refused.
    """
    assert not issubclass(cli.UnwritableKey, cli.UnsafePath)
    assert not issubclass(cli.UnsafePath, cli.UnwritableKey)
    assert issubclass(cli.UnwritableKey, ValueError)


# ── a configuration nobody can read is reported, not raised ────────────────


@pytest.mark.parametrize("block", [["local"], "local", 5])
def test_a_providers_value_that_is_not_a_block_reports_instead_of_raising(block):
    """Contract divergence (15), on the axis its own entry did not name.

    A *truthy* non-block reached `resolve_route`'s `.get` and raised
    `AttributeError`, which is not the `ConfigError` the workbench's routing
    projection catches — so both projections fell together, not only the provider
    list. The falsy spellings were always absorbed by the `or {}` beside it,
    which is why this survived being written down.
    """
    from scriptorium.providers import available
    cfg = {**DEFAULT_CONFIG, "providers": block}
    assert available(cfg) == []
    with pytest.raises(ConfigError):
        resolve_route(cfg, "draft")


def test_one_unreadable_provider_does_not_cost_the_others():
    from scriptorium.providers import available
    rows = {r["name"]: r for r in available(
        {"providers": {"good": {"kind": "openai", "model": "m"}, "bad": "oops"}})}
    assert "error" not in rows["good"] and rows["good"]["model"] == "m"
    assert "providers.bad" in rows["bad"]["error"]


@pytest.mark.parametrize("spec,why", [
    ({"api_key_env": 5}, "api_key_env"),
    ("not a block", "block"),
])
def test_a_spec_whose_credential_field_is_unreadable_is_never_green(spec, why):
    """`needs_key: false` with `key_present: true` is how "no key needed" looks.

    Folding an unreadable `api_key_env` to `""` produces exactly that pair, which
    would tell a person their backend is ready when nothing has been decided.
    """
    from scriptorium.providers import available
    row = available({"providers": {"x": spec}})[0]
    assert why in row["error"]
    assert row["needs_key"] is True and row["key_present"] is False


def test_a_readable_credential_field_keeps_its_answer_when_a_neighbour_is_broken():
    """A malformed `model` does not make "this backend wants no key" untrue."""
    from scriptorium.providers import available
    row = available({"providers": {"x": {"model": 5, "api_key_env": ""}}})[0]
    assert "model" in row["error"]
    assert row["needs_key"] is False and row["key_present"] is True


def test_the_typed_readback_masks_exactly_what_the_printed_one_masks():
    """`do_config_value` is `do_config_get` with the type kept, not with the rules dropped.

    A reply body is a display surface, and this is the function that fills one.
    It is tested here rather than through the endpoint because the endpoint
    cannot reach the masking on its success path — the validator that runs just
    before refuses exactly what the projection would mask — so a test written
    over the wire proves nothing about this function. A mutation run is what said
    so: the projection came out and every endpoint test went on passing.
    """
    cfg = {"providers": {"gw": {"base_url": "https://gw.example.com/v1?key=SEKRIT",
                                "api_key_env": "sk-not-a-name",
                                "headers": {"Authorization": "Bearer SEKRIT"}}},
           "batch": {"size": 12}}
    assert "SEKRIT" not in str(cli.do_config_value(cfg, "providers.gw.base_url"))
    assert "gw.example.com" in cli.do_config_value(cfg, "providers.gw.base_url")
    assert "SEKRIT" not in str(cli.do_config_value(cfg, "providers.gw.headers"))
    assert cli.do_config_value(cfg, "providers.gw.api_key_env") == cli._NOT_A_NAME
    # The type is the whole reason this exists beside `do_config_get`, which
    # would answer the string "12" and make every client parse a number back out
    # of one.
    assert cli.do_config_value(cfg, "batch.size") == 12
    assert cli.do_config_value(cfg, "batch.nothing") is None


def test_printable_url_reads_a_bad_port_inside_its_own_guard():
    """`SplitResult.port` is a *lazy property* — it parses on access.

    So `if parsed.port:` raised `ValueError` from outside the `try` that exists
    to contain exactly that. Measured 2026-09-01: one hand-edited
    `https://user:SECRET@host:notaport/v1` answered `400` on `/api/state` **and**
    on `/api/models`, and gave `lx providers` a traceback and exit 1 instead of
    this project's one sentence and exit 2. The masking function crashed inside
    the mask.
    """
    from scriptorium.config import printable_url

    secret = "SUPERSECRETPASSWORD"
    out = printable_url(f"https://alice:{secret}@example.invalid:notaport/v1")
    assert secret not in out
    # Nothing about it is printable, so nothing of it is printed. Returning the
    # value verbatim was the other option and is wrong in the one case this
    # function exists for.
    assert "example.invalid" not in out
    assert printable_url("https://alice:s3cr3t@example.invalid:8443/v1") == \
        "https://example.invalid:8443/v1"


# ── no refusal repeats the value it refused ───────────────────────────────
#
# Invariant 6 since 2026-09-13: a refusal of a value names the field, what is
# accepted and — for a non-string — its shape, and never the value or its length,
# in every field and not only in the three a key was expected to land in. Until
# then `_field_kind`, `_as_text`, `_as_number`, `_as_count`, `_field_route`,
# `_field_embedding_provider`, `_field_tone`, `_decode` and both refusals in
# `providers.build` quoted what they refused (`docs/contracts/workbench-http.md`
# divergence (29)). And a field whose value it can never legally hold is not
# displayed either: a hand-edited `kind`, `api_key_env` or `base_url`.
#
# Three halves, each blind to something the others see. The sweep reads what a
# person would read, over every pattern the field table has *today and later*,
# since it iterates the table. The `ast` guard reads how every refusal is built,
# including branches no value in the sweep reaches. The surface tests carry the
# sentence through `lx` itself. `tests/test_web.py` holds the wire's half.

from test_provider import _windows  # noqa: E402

#: HANDOFF-077's value: 32 characters, key-shaped, and lower-case with a hyphen,
#: so `api_key_env`'s shape rule refuses it rather than storing it. An upper-case
#: token of that length is a legal variable name and is stored — a residual
#: `_field_api_key_env`'s docstring already records, not something this tests.
KEYLIKE = "sk-PASTEDabcdefghijklmnopqrstuv1"

#: Every `(pattern, shape)` the sweep finds **storing** the value instead of
#: refusing it — **when nothing declares it**. These are not refusals, so no
#: sentence repeats anything. Each pair is allowed because a value the
#: configuration does not declare a credential is text, and refusing text by
#: its shape refuses a model id a backend serves (HANDOFF-080's red line: the
#: package's own model ids include `mradermacher/…-GGUF:Q4_K_M`); the same
#: pairs are the ones `refuse_credential` reaches, and the sweep below this one
#: exports the value under a declared name and asserts the set is then empty. A
#: rule added later that stores one fails this test by default, and somebody
#: decides it here.
STORES_A_KEY = {
    ("providers.*.model", "text"), ("providers.*.model", "padded"),
    ("providers.*.model", "text:model"), ("providers.*.model", "provider:text"),
    ("providers.*.model", "json-list"), ("providers.*.model", "json-block"),
    ("routing.*", "provider:text"), ("routing.*", "model-block"),
    ("tone", "text"), ("tone", "padded"), ("tone", "text:model"),
    ("tone", "provider:text"), ("tone", "json-list"), ("tone", "json-block"),
}


def _shapes(value):
    """The ways a value reaches a field rule: a word, a word with a colon, and JSON."""
    return {
        "text": value,
        "padded": f"  {value}\n",
        "text:model": f"{value}:m",
        "provider:text": f"p:{value}",
        "list": [value],
        "block": {"x": value},
        "provider-block": {"provider": value},
        "model-block": {"provider": "p", "model": value},
        "json-list": json.dumps([value]),
        "json-block": json.dumps({"provider": value}),
    }


def _sweep_cfg():
    return {**DEFAULT_CONFIG, "providers": {**DEFAULT_CONFIG["providers"],
                                            "p": {"kind": "openai", "model": "m"}}}


def _addressed(pattern):
    return pattern.replace("routing.*", "routing.draft").replace("*", "p")


def test_no_field_rule_repeats_a_value_it_refuses_and_only_the_pinned_ones_store_it(monkeypatch):
    """Every pattern in the field table, every shape, the leaf spelling and the block spelling.

    The table is iterated rather than listed, so a field rule added later is
    swept the day it is registered — which is the only moment anybody could
    notice that it quotes what it refuses. The two names the shipped providers
    declare are removed from the environment first, so "nothing declares the
    value" is this test's own premise and not the runner's luck.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg, stored, refused = _sweep_cfg(), set(), 0
    for pattern in cli._CONFIG_FIELDS:
        key = _addressed(pattern)
        parent, _, leaf = key.rpartition(".")
        for label, shape in _shapes(KEYLIKE).items():
            spellings = [(key, shape)]
            if parent:
                spellings.append((parent, json.dumps({leaf: shape})))
            for address, raw in spellings:
                try:
                    parts, value = cli.config_value(cfg, address, raw)
                except ConfigError as e:
                    refused += 1
                    assert _windows(KEYLIKE, str(e)) == [], (pattern, label, address, str(e))
                    continue
                if _windows(KEYLIKE, json.dumps(value)) and address == key:
                    stored.add((pattern, label))
    assert refused > 100, refused
    assert stored == STORES_A_KEY, (
        "a field rule changed whether it stores a key-shaped value — HANDOFF-080 "
        f"owns this set: gained {stored - STORES_A_KEY}, lost {STORES_A_KEY - stored}")


@pytest.mark.parametrize("current", [True, 3, 2.5])
def test_a_key_with_no_rule_is_refused_by_its_type_without_being_repeated(current):
    """`_decode`'s branches: a boolean a hand-edited file holds, and a number."""
    cfg = {**DEFAULT_CONFIG, "handmade": current}
    with pytest.raises(ConfigError) as caught:
        cli.config_value(cfg, "handmade", KEYLIKE)
    assert _windows(KEYLIKE, str(caught.value)) == [], str(caught.value)
    assert "handmade" in str(caught.value)


def test_a_non_string_value_is_named_by_its_shape():
    """What a refusal keeps when it loses the value: the field, the shape, the accepted set."""
    cfg = _sweep_cfg()
    for raw, shape in (([KEYLIKE], "a list"), ({"x": KEYLIKE}, "a block"),
                       (None, "null"), (7, "a number"), ("   ", "blank text")):
        with pytest.raises(ConfigError) as caught:
            cli.config_value(cfg, "providers.p.kind", raw)
        assert shape in str(caught.value) and "providers.p.kind" in str(caught.value)
    with pytest.raises(ConfigError) as caught:
        cli.config_value(cfg, "providers.p.kind", KEYLIKE)
    assert str(caught.value) == ("providers.p.kind is not a backend this build has. "
                                 "Accepted: anthropic, openai, openai-compatible.")


def test_build_repeats_neither_the_name_nor_the_kind_it_refuses():
    """`providers.build` is outside the `ast` guard, so both of its refusals are read here."""
    from scriptorium.providers import ProviderError, build
    cfg = _sweep_cfg()
    with pytest.raises(ProviderError) as caught:
        build(KEYLIKE, cfg)
    # The whole sentence, not a window: the rule is "not its length" too, and a
    # sentence that grew a character count passed every window assertion.
    assert str(caught.value) == ("unknown provider: nothing is configured under that name. "
                                 "Configured: claude, llamacpp, lmstudio, local, openai, p")
    for kind in (KEYLIKE, [KEYLIKE], ""):
        with pytest.raises(ProviderError) as caught:
            build("p", {"providers": {"p": {"kind": kind, "model": "m"}}})
        assert str(caught.value) == (
            "providers.p.kind is not a backend this build has. Accepted: anthropic, openai, "
            "openai-compatible — fix it in lx.config.json; `lx providers` names the row.")


def test_a_field_holding_what_it_never_may_is_not_displayed():
    """`kind`, `api_key_env` and `base_url`, hand-edited, on both display functions.

    `providers.available` feeds `lx providers`, `/api/state` and every
    `POST /api/config` reply; `cli._printable` feeds `lx config get` and the
    typed readback. They answered differently for `api_key_env` until 2026-09-13
    — masked by one, printed whole by the other — and neither masked the other
    two fields at all.
    """
    from scriptorium.config import NOT_AN_ADDRESS
    from scriptorium.providers import ProviderError, available, build
    specs = {"k": {"kind": KEYLIKE, "model": "m"},
             "e": {"kind": "openai", "api_key_env": KEYLIKE},
             "u": {"kind": "openai", "base_url": KEYLIKE},
             "kk": {"kind": {"x": KEYLIKE}, "model": "m"},
             "ee": {"kind": "openai", "api_key_env": {"x": KEYLIKE}},
             "ok": {"kind": "anthropic", "base_url": "https://api.anthropic.com",
                    "api_key_env": "ANTHROPIC_API_KEY"},
             "bare": {"kind": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
             "blank": {"kind": "openai", "model": "m", "base_url": ""},
             "spaces": {"kind": "openai", "model": "m", "base_url": "   "},
             "unread": {"kind": "openai", "model": "m", "base_url": "http://[::1"},
             "lst": {"kind": "openai", "model": "m", "base_url": [KEYLIKE]},
             "ws": {"kind": "openai", "model": "m", "base_url": "  http://127.0.0.1:9/v1\n"}}
    cfg = {**DEFAULT_CONFIG, "providers": specs}
    rows = {row["name"]: row for row in available(cfg)}
    assert _windows(KEYLIKE, json.dumps(rows)) == [], rows
    # Surrounding whitespace is a row the transport refuses as written —
    # `Provider._request` tests the raw prefix — and it used to show as a clean
    # address with no `error`, because `urlsplit` reads past the blank. Found by
    # the security-tier review of HANDOFF-078; decided under HANDOFF-080.
    assert rows["ws"]["base_url"] == "http://127.0.0.1:9/v1", rows["ws"]
    assert "whitespace" in rows["ws"]["error"], rows["ws"]
    # And `lx config get` agrees with the row, so the two commands answer one value one way.
    assert cli.do_config_get(cfg, "providers.ws.base_url") == "http://127.0.0.1:9/v1"
    assert cli.do_config_value(cfg, "providers.ws")["base_url"] == "http://127.0.0.1:9/v1"
    assert rows["k"]["kind"] == "" and "kind" in rows["k"]["error"]
    assert rows["e"]["key_env"] == "" and "api_key_env" in rows["e"]["error"]
    assert rows["e"]["needs_key"] is True and rows["e"]["key_present"] is False
    assert rows["u"]["base_url"] == NOT_AN_ADDRESS and "base_url" in rows["u"]["error"]
    assert rows["ok"]["kind"] == "anthropic" and "error" not in rows["ok"]
    assert rows["ok"]["key_env"] == "ANTHROPIC_API_KEY"
    # **No `base_url` at all is a working backend**: the class supplies its
    # default. The first version of this change described it as not an address,
    # with no error, on every surface — found by the security-tier
    # re-derivation of 2026-09-13.
    assert rows["bare"]["base_url"] == "" and "error" not in rows["bare"], rows["bare"]
    # A *present* blank one is not: the class reads `spec.get("base_url", default)`
    # and sends the empty string, which `_request` refuses.
    assert rows["blank"]["base_url"] == "" and "base_url" in rows["blank"]["error"]
    assert rows["spaces"]["base_url"] == "" and "base_url" in rows["spaces"]["error"]
    # One the parser cannot read is no more usable than one that is not an address.
    assert rows["unread"]["base_url"] == "(unreadable base_url)"
    assert "base_url" in rows["unread"]["error"]
    # A list where the URL belongs — found by the security-tier re-derivation,
    # printed whole by `lx config get` and a traceback from `lx models`.
    assert rows["lst"]["base_url"] == "" and "base_url" in rows["lst"]["error"]
    assert cli.do_config_get(cfg, "providers.lst.base_url") == NOT_AN_ADDRESS
    assert cli.do_config_value(cfg, "providers.lst")["base_url"] == NOT_AN_ADDRESS
    with pytest.raises(ProviderError) as caught:
        build("lst", cfg)
    assert str(caught.value) == (
        "providers.lst.base_url is an http:// or https:// address, as text — fix it in "
        "lx.config.json; `lx providers` names the row.")
    assert NOT_AN_ADDRESS not in build("bare", cfg).describe()

    shown = cli.do_config_get(cfg, "providers")
    assert _windows(KEYLIKE, shown) == [], shown
    assert cli.do_config_get(cfg, "providers.k.kind") == cli._NOT_A_KIND
    assert cli.do_config_value(cfg, "providers.u.base_url") == NOT_AN_ADDRESS
    assert cli.do_config_get(cfg, "providers.ok.kind") == "anthropic"
    # A block where a single value belongs, read whole and one level down: the
    # masks answer at every depth under the field, not only at the field.
    for key, mask in (("providers.kk", None), ("providers.kk.kind", cli._NOT_A_KIND),
                      ("providers.kk.kind.x", cli._NOT_A_KIND), ("providers.ee", None),
                      ("providers.ee.api_key_env.x", cli._NOT_A_NAME)):
        out = cli.do_config_get(cfg, key)
        assert _windows(KEYLIKE, out) == [], (key, out)
        assert mask is None or out == mask, (key, out)


@pytest.mark.parametrize("url", [KEYLIKE, "file:///x", "ftp://host/v1", "localhost:11434/v1",
                                 "https:///v1"])
def test_printable_url_prints_nothing_of_what_is_not_an_http_address(url):
    """The test `_field_base_url` refuses a write with, applied to what may be shown."""
    from scriptorium.config import NOT_AN_ADDRESS, printable_url
    assert printable_url(url) == NOT_AN_ADDRESS
    assert printable_url("http://localhost:11434/v1") == "http://localhost:11434/v1"
    assert printable_url(" https://api.openai.com/v1") == " https://api.openai.com/v1"
    # Nothing is not a non-address: a block with no `base_url` hands this `""`.
    assert printable_url("") == "" and printable_url("  ") == "  "
    # And an IPv6 literal keeps its brackets when userinfo is taken off it, with a
    # port and without one.
    assert printable_url(f"http://u:{KEYLIKE}@[::1]:8080/v1") == "http://[::1]:8080/v1"
    assert printable_url(f"http://u:{KEYLIKE}@[::1]/v1") == "http://[::1]/v1"
    # A password with no username is the spelling some gateways document, and a
    # test for it was missing: dropping `parsed.password` from the check printed
    # the whole key with the suite green — measured by the security-tier
    # re-derivation of 2026-09-13.
    assert printable_url(f"http://:{KEYLIKE}@host/v1") == "http://host/v1"
    assert printable_url([KEYLIKE]) == NOT_AN_ADDRESS and printable_url(None) == NOT_AN_ADDRESS


def test_lx_repeats_no_part_of_a_key_pasted_into_a_box_beside_the_credential_fields(tmp_path):
    """HANDOFF-077's criterion 3, and the same value through every command that reads it back.

    `lx config set providers.p.kind <key>` first, as the package measured it —
    then a hand-edited file carrying the value in `kind`, `api_key_env` and
    `base_url`, read by the commands a person runs to see what is wrong, and the
    name refused by `--provider`.
    """
    env = _project(tmp_path)
    r = _lx(["config", "set", "providers.p.kind", KEYLIKE], tmp_path, env)
    assert r.returncode == 2, _both(r)
    assert _windows(KEYLIKE, _both(r)) == [], _both(r)
    assert "Accepted: anthropic, openai, openai-compatible" in _err(r)

    data = _config(tmp_path)
    data["providers"] = {"k": {"kind": KEYLIKE, "model": "m"},
                         "e": {"kind": "openai", "api_key_env": KEYLIKE},
                         "u": {"kind": "openai", "base_url": KEYLIKE}}
    (tmp_path / "lx.config.json").write_text(json.dumps(data), encoding="utf-8")
    for args, code in ((["providers"], 0), (["config", "get", "providers"], 0),
                       (["models", "--provider", "k"], 2),
                       (["models", "--provider", KEYLIKE], 2)):
        r = _lx(args, tmp_path, env)
        assert r.returncode == code, (args, _both(r))
        assert "Traceback" not in _err(r), (args, _err(r))
        assert _windows(KEYLIKE, _both(r)) == [], (args, _both(r))


# The `ast` half. What flows from a value parameter — `value`, `raw`, `v` — may
# reach a `raise` in a function a configuration value can reach only as an
# argument to `_shape_of`, or as a decoder's position (`e.msg`, `e.lineno`,
# `e.colno`, `e.pos`), and it may not be handed to another such function in any
# parameter but its value parameter. What it cannot see: a value read back out of
# `cfg` rather than received, a helper outside `cli.py`, and a message a callee
# outside the closure builds — `providers.build` is the one of those this
# project has, and the test above reads its sentences instead.

#: `secret` joined on 2026-09-13 with HANDOFF-080: a credential the
#: configuration declares is read out of the environment and out of `cfg`, and
#: the function that compares a written leaf against one takes it under that
#: name — so a parameter called `secret` is a value in the guard's sense, and a
#: raise that reached one, or a helper handed one under any other name, fails.
_VALUE_PARAMS = frozenset({"value", "raw", "v", "secret"})
_POSITION_ATTRS = frozenset({"msg", "lineno", "colno", "pos"})


def _refusal_closure():
    import ast
    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    defs = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    table = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(getattr(t, "id", None) == "_CONFIG_FIELDS" for t in node.targets))
    todo = ["config_value"] + [node.id for entry in table.values for node in ast.walk(entry)
                               if isinstance(node, ast.Name) and node.id in defs]
    seen = {}
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen[name] = defs[name]
        todo += [node.func.id for node in ast.walk(defs[name]) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id in defs]
    # The table's own lambdas are functions a value reaches too — a lambda that
    # handed the value to `_as_text`'s `what` was invisible to the first version
    # of this guard, and only the sweeps caught it.
    lambdas = {f"_CONFIG_FIELDS[{key.value}]": entry
               for key, entry in zip(table.keys, table.values) if isinstance(entry, ast.Lambda)}
    return seen, lambdas


def _is_environment(node):
    """`os.environ` or `os.getenv(…)`: what the environment holds is a credential here."""
    import ast
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return getattr(node.value, "id", None) == "os"
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv" and getattr(node.func.value, "id", None) == "os")


def _tainted_names(fn):
    import ast
    # `_field_api_key_env` compares a value against every exported variable and
    # names the one that matched — the one refusal holding a real secret. The
    # environment is a taint source, so the variable's *content* may not reach a
    # raise while its name, a key of the mapping, still may.
    tainted = {a.arg for a in fn.args.args if a.arg in _VALUE_PARAMS} | {"os.environ"}

    def names(node):
        found = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        return found | ({"os.environ"} if any(map(_is_environment, ast.walk(node))) else set())

    def targets(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    while True:
        before = set(tainted)
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                if node.value is not None and names(node.value) & tainted:
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        tainted |= targets(target)
            elif isinstance(node, (ast.For, ast.comprehension)):
                if not names(node.iter) & tainted:
                    continue
                # A block's *keys* become the address of the field below them —
                # `lx config set providers '{"x": {"kind": …}}'` is refused as
                # `providers.x.kind` — and the contract's rule is that a key name
                # is not a value. So iterating `.items()` taints what is held, not
                # what it is held under, and `.keys()` taints nothing.
                method = getattr(getattr(node.iter, "func", None), "attr", None)
                if method == "keys":
                    continue
                if (method == "items" and isinstance(node.target, ast.Tuple)
                        and len(node.target.elts) == 2):
                    tainted |= targets(node.target.elts[1])
                else:
                    tainted |= targets(node.target)
            elif isinstance(node, ast.withitem):
                if node.optional_vars is not None and names(node.context_expr) & tainted:
                    tainted |= targets(node.optional_vars)
            elif isinstance(node, ast.Try):
                if any(names(stmt) & tainted for stmt in node.body):
                    tainted |= {h.name for h in node.handlers if h.name}
        if tainted == before:
            return tainted


def test_a_refusal_in_the_configuration_writer_is_built_from_nothing_it_was_handed():
    import ast
    closure, lambdas = _refusal_closure()
    assert {"_field_kind", "_field_route", "_as_text", "_as_number", "_decode",
            "_as_block", "_field_tone", "_field_api_key_env",
            # HANDOFF-080's credential rule and everything it reads through.
            "refuse_credential", "_refuse_credential_names", "_declared_credentials",
            "_names_declared_inside", "_new_names", "_matches", "_leaves", "_fold",
            } <= set(closure), sorted(closure)
    assert "_CONFIG_FIELDS[providers.*.model]" in lambdas, sorted(lambdas)
    problems = []
    for name, fn in {**closure, **lambdas}.items():
        tainted = _tainted_names(fn)
        parents = {child: node for node in ast.walk(fn) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(fn):
            if isinstance(node, ast.Raise):
                for part in (node.exc, node.cause):
                    for leak in ast.walk(part) if part is not None else ():
                        if _is_environment(leak):
                            problems.append(f"{name}:{node.lineno} raises with the environment")
                            continue
                        if not (isinstance(leak, ast.Name) and leak.id in tainted):
                            continue
                        up = parents.get(leak)
                        if isinstance(up, ast.Attribute) and up.attr in _POSITION_ATTRS:
                            continue
                        if (isinstance(up, ast.Call) and isinstance(up.func, ast.Name)
                                and up.func.id == "_shape_of"):
                            continue
                        problems.append(f"{name}:{node.lineno} raises with {leak.id}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in ("print", "_out", "_err")):
                # A sink beside `raise`: a leak through stdout was caught only
                # by the subprocess tests until 2026-09-13.
                if any(isinstance(n, ast.Name) and n.id in tainted
                       for arg in node.args for n in ast.walk(arg)):
                    problems.append(f"{name}:{node.lineno} prints a value")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in closure):
                params = [a.arg for a in closure[node.func.id].args.args]
                bound = list(zip(params, node.args)) + [(k.arg, k.value) for k in node.keywords]
                for param, arg in bound:
                    if param not in _VALUE_PARAMS and any(
                            isinstance(n, ast.Name) and n.id in tainted for n in ast.walk(arg)):
                        problems.append(
                            f"{name}:{node.lineno} hands a value to {node.func.id}({param}=…)")
    assert problems == [], problems


# ── HANDOFF-080: a box that accepts text does not store a declared credential ──
#
# The rule is `cli.refuse_credential`, asked by `config_value` of every write,
# by `do_extract` of the register and the language, and by `do_glossary_set`.
# It compares against what the configuration *declares* — the content of the
# variable each `api_key_env` names, a `headers` value, a `base_url` userinfo —
# and nothing wider, which is what keeps every model id a backend serves
# writable. The `ast` guard above cannot see two of the three sources (they are
# read out of `cfg`), so each is held here at runtime, on both spellings, with
# the sentence pinned whole: `_windows` is blind to a prefix shorter than eight
# characters and to a length, and both have passed it before.

#: What `providers.p.model` answers when the shipped `openai` provider declares
#: `OPENAI_API_KEY` and that variable holds the value. Pinned as a literal, not
#: rebuilt from `cli._CREDENTIAL_ADVICE`, so a sentence that grew a length or a
#: prefix fails here where every window assertion passes it.
SAYS_DECLARED = (
    "providers.p.model was given the content of OPENAI_API_KEY, which "
    "providers.openai.api_key_env names as a key — the value is not repeated here, "
    "because that is what it is. A key belongs in the environment, named by a "
    "provider's api_key_env, and nowhere in lx.config.json. If OPENAI_API_KEY holds a "
    "placeholder rather than a key, change what it holds (a running lx web read it at "
    "start — restart it), or stop naming it: `lx config set "
    "providers.openai.api_key_env \"\"` — in PowerShell 5.1, '\"\"'. Never `unset`, "
    "which puts a shipped provider's default name back.")

#: A credential every eight-character window of which carries an upper-case
#: letter: `canonical_tone` lower-cases the register on its way into the memory
#: file, and an oracle that forgot to fold case passes on the folded copy.
MIXED = "sk-MiXeDcAsEkEyAbCdEfGhIjKlMnOpQ"

#: The model ids the package lists, every one of which a backend really serves.
MODEL_IDS = ["qwen2.5:14b-instruct", "local-model", "gpt-4o-mini", "claude-sonnet-4-6",
             "unsloth/Qwen3.6-35B-A3B-GGUF:IQ2_M",
             "mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M",
             "ScrambieBambie_Snowpiercer-15B-v2_Q8_0"]


def _folded(secret, text):
    return _windows(secret.lower(), text.lower())


def test_a_declared_credential_is_refused_in_every_field_and_no_refusal_carries_it(monkeypatch):
    """The sweep above, with the value exported under a name the configuration declares.

    Every pattern, every shape, both spellings: nothing stores, and no refusal
    carries a window. The shapes `STORES_A_KEY` pins are exactly the ones that
    reach `refuse_credential` — every other is refused by the field's own rule
    first — so those name the variable and the provider that declares it.
    """
    monkeypatch.setenv("OPENAI_API_KEY", KEYLIKE)
    cfg, stored, named = _sweep_cfg(), set(), set()
    for pattern in cli._CONFIG_FIELDS:
        key = _addressed(pattern)
        parent, _, leaf = key.rpartition(".")
        for label, shape in _shapes(KEYLIKE).items():
            spellings = [(key, shape)]
            if parent:
                spellings.append((parent, json.dumps({leaf: shape})))
            for address, raw in spellings:
                try:
                    parts, value = cli.config_value(cfg, address, raw)
                except ConfigError as e:
                    assert _windows(KEYLIKE, str(e)) == [], (pattern, label, address, str(e))
                    if "which providers.openai.api_key_env names" in str(e):
                        named.add((pattern, label))
                    continue
                stored.add((pattern, label, address))
    assert stored == set(), stored
    assert named >= STORES_A_KEY, STORES_A_KEY - named


def test_the_credential_refusal_is_one_exact_sentence_on_each_field(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEYLIKE)
    cfg = _sweep_cfg()
    for key, raw in (("providers.p.model", KEYLIKE), ("providers.p.model", f"  {KEYLIKE}\n"),
                     ("routing.draft", f"p:{KEYLIKE}"),
                     ("routing.draft", json.dumps({"provider": "p", "model": KEYLIKE})),
                     ("tone", KEYLIKE), ("targets", KEYLIKE), ("source_lang", KEYLIKE),
                     ("handmade", KEYLIKE)):
        with pytest.raises(ConfigError) as caught:
            cli.config_value(cfg, key, raw)
        assert str(caught.value) == SAYS_DECLARED.replace("providers.p.model", key, 1), (key, raw)


def test_a_wrapped_paste_is_refused_and_a_short_match_is_not(monkeypatch):
    """Containment from `_ENV_LONG`, equality from `_ENV_CONTENT_FLOOR`, and nothing between.

    The five ways a key is really pasted besides bare — quoted, `Bearer …`, the
    `.env` line, the `export` line, padded — all carry the 32-character key and
    are refused. An eight-character *piece* of it is not, and a nine-character
    placeholder inside a longer model id is not: `lm-studio` sits inside
    `lm-studio-community/…`, which is the false positive containment at eight
    would have had.
    """
    monkeypatch.setenv("OPENAI_API_KEY", KEYLIKE)
    cfg = _sweep_cfg()
    for raw in (f'"{KEYLIKE}"', f"Bearer {KEYLIKE}", f"OPENAI_API_KEY={KEYLIKE}",
                f"export OPENAI_API_KEY={KEYLIKE}", f"  {KEYLIKE}  "):
        with pytest.raises(ConfigError) as caught:
            cli.config_value(cfg, "providers.p.model", raw)
        assert _windows(KEYLIKE, str(caught.value)) == [], str(caught.value)
    assert cli.config_value(cfg, "providers.p.model", KEYLIKE[3:11])[1] == KEYLIKE[3:11]
    monkeypatch.setenv("OPENAI_API_KEY", "lm-studio")
    assert cli.config_value(cfg, "providers.p.model", "lm-studio-community/Meta-Llama-3-8B")[1] \
        == "lm-studio-community/Meta-Llama-3-8B"
    with pytest.raises(ConfigError):
        cli.config_value(cfg, "providers.p.model", "lm-studio")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")            # below the floor
    assert cli.config_value(cfg, "providers.p.model", "ollama")[1] == "ollama"
    monkeypatch.setenv("OPENAI_API_KEY", f"  {KEYLIKE}\n")     # exported with a clipboard's newline
    with pytest.raises(ConfigError):
        cli.config_value(cfg, "providers.p.model", KEYLIKE)


def test_every_listed_model_id_is_written_whatever_the_environment_holds(tmp_path):
    """Criterion 3: the red line, on the terminal. The wire's half is in `tests/test_web.py`."""
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE, ANTHROPIC_API_KEY=BARE_TOKEN))
    for model in MODEL_IDS:
        r = _lx(["config", "set", "providers.openai.model", model], tmp_path, env)
        assert r.returncode == 0, (model, _err(r))
        assert _config(tmp_path)["providers"]["openai"]["model"] == model
        r = _lx(["routing", "set", "draft", f"openai:{model}"], tmp_path, env)
        assert r.returncode == 0, (model, _err(r))
        assert _config(tmp_path)["routing"]["draft"] == {"provider": "openai", "model": model}


@pytest.mark.parametrize("args", [
    ["config", "set", "providers.openai.model", KEYLIKE],
    ["config", "set", "providers.openai.model", f"Bearer {KEYLIKE}"],
    ["routing", "set", "draft", f"openai:{KEYLIKE}"],
    ["config", "set", "routing.draft", json.dumps({"provider": "openai", "model": KEYLIKE})],
    ["config", "set", "tone", KEYLIKE],
    ["config", "set", "targets", KEYLIKE],
    ["config", "set", "source_lang", KEYLIKE],
    ["config", "set", "providers.openai", json.dumps({"kind": "openai", "model": KEYLIKE})],
    ["config", "set", "providers", json.dumps({"gw": {"kind": "openai", "model": KEYLIKE}})],
])
def test_lx_refuses_a_declared_credential_in_every_box_and_leaves_the_file(tmp_path, args):
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    before = (tmp_path / "lx.config.json").read_bytes()
    r = _lx(args, tmp_path, env)
    assert r.returncode == 2, (args, _both(r))
    assert "Traceback" not in _err(r)
    assert _windows(KEYLIKE, _both(r)) == [], _both(r)
    assert "OPENAI_API_KEY" in _err(r) and "providers.openai.api_key_env" in _err(r), _err(r)
    assert (tmp_path / "lx.config.json").read_bytes() == before


def test_a_digit_only_credential_is_read_off_the_raw_text_of_a_number_field(tmp_path):
    """`float()` has lost a leading zero and any precision past 17 digits by the time the rule returns."""
    digits = "00012345678901234567890"
    env = _project(tmp_path, _env(OPENAI_API_KEY=digits))
    r = _lx(["config", "set", "providers.openai.timeout", digits], tmp_path, env)
    assert r.returncode == 2 and digits not in _both(r), _both(r)
    r = _lx(["config", "set", "batch.size", digits], tmp_path, env)
    assert r.returncode == 2 and digits not in _both(r), _both(r)
    r = _lx(["config", "set", "providers.openai.timeout", "300"], tmp_path, env)
    assert r.returncode == 0, _err(r)


def test_the_one_false_positive_names_the_variable_and_its_remedy_runs(tmp_path):
    """A declared variable holding a model id is a misconfiguration the refusal names.

    The remedy printed is run verbatim, in the state it was printed in — a
    refusal whose remedy is the wrong command is worse than none — and it is
    `lx config set … ""` rather than `unset`, because `unset` on a shipped
    provider puts the default name straight back (measured).
    """
    env = _project(tmp_path, _env(OPENAI_API_KEY="gpt-4o-mini"))
    r = _lx(["config", "set", "providers.openai.model", "gpt-4o-mini"], tmp_path, env)
    assert r.returncode == 2 and "OPENAI_API_KEY holds a placeholder" in _err(r), _err(r)
    assert 'lx config set providers.openai.api_key_env ""' in _err(r)
    r = _lx(["config", "set", "providers.openai.api_key_env", ""], tmp_path, env)
    assert r.returncode == 0, _err(r)
    r = _lx(["config", "set", "providers.openai.model", "gpt-4o-mini"], tmp_path, env)
    assert r.returncode == 0, _err(r)
    assert _config(tmp_path)["providers"]["openai"]["model"] == "gpt-4o-mini"
    # `unset` is what the sentence says not to run, and this is why.
    r = _lx(["config", "unset", "providers.openai.api_key_env"], tmp_path, env)
    assert r.returncode == 0
    r = _lx(["config", "set", "providers.openai.model", "gpt-4o-mini"], tmp_path, env)
    assert r.returncode == 2, _both(r)


def test_a_variable_whose_content_is_its_own_name_declares_nothing(tmp_path):
    """The `docker run -e $TOKEN` residual `_field_api_key_env` keeps, kept here too."""
    env = _project(tmp_path, _env(OPENAI_API_KEY="OPENAI_API_KEY"))
    r = _lx(["config", "set", "providers.openai.api_key_env", "OPENAI_API_KEY"], tmp_path, env)
    assert r.returncode == 0, _err(r)
    r = _lx(["config", "set", "providers.openai.model", "OPENAI_API_KEY"], tmp_path, env)
    assert r.returncode == 0, _err(r)


@pytest.mark.parametrize("secret", ["S3CRETVALUE9X", "s3cretvalue9x-with-more-than-twenty"])
def test_a_headers_value_and_a_base_url_userinfo_are_declared_credentials_too(secret, monkeypatch):
    """The two sources the `ast` guard cannot see, in every spelling `Provider._credentials` reads.

    An upper-case secret as well as a long one: `_windows` is case-sensitive,
    and the containment arm only opens at twenty characters.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    quoted = urllib.parse.quote(secret + "/=+", safe="")
    sources = {
        "headers whole": ({"headers": {"x-api-key": secret}}, secret,
                          "a header value of providers.gw.headers"),
        "headers bearer": ({"headers": {"Authorization": f"Bearer {secret}"}}, secret,
                           "a header value of providers.gw.headers"),
        "headers int": ({"headers": {"X-Int": 12345678901}}, "12345678901",
                        "a header value of providers.gw.headers"),
        "password only": ({"base_url": f"http://:{secret}@127.0.0.1:9/v1"}, secret,
                          "the userinfo of providers.gw.base_url"),
        "user:password": ({"base_url": f"http://u:{secret}@127.0.0.1:9/v1"}, secret,
                          "the userinfo of providers.gw.base_url"),
        "token only": ({"base_url": f"http://{secret}@127.0.0.1:9/v1"}, secret,
                       "the userinfo of providers.gw.base_url"),
        "unquoted": ({"base_url": f"http://u:{quoted}@127.0.0.1:9/v1"}, secret + "/=+",
                     "the userinfo of providers.gw.base_url"),
    }
    for label, (spec, pasted, where) in sources.items():
        cfg = {**DEFAULT_CONFIG, "providers": {
            **DEFAULT_CONFIG["providers"], "p": {"kind": "openai", "model": "m"},
            "gw": {"kind": "openai", "model": "m", **spec}}}
        for key in ("providers.p.model", "tone"):
            with pytest.raises(ConfigError) as caught:
                cli.config_value(cfg, key, pasted)
            said = str(caught.value)
            assert _windows(pasted, said) == [] and _windows(secret, said) == [], (label, said)
            assert said.startswith(f"{key} was given {where} — the value is not repeated"), (label, said)
            assert "lx.config.json" in said
        # The pasted value beside the secret, not inside it: a leaf that holds
        # the header's *scheme* too is caught by the whole-value form.
        if label == "headers bearer":
            with pytest.raises(ConfigError):
                cli.config_value(cfg, "providers.p.model", f"Bearer {secret}")


def test_a_hand_edited_shape_no_rule_can_read_is_skipped_rather_than_raised_on(tmp_path):
    """`lx config set` is the command a person runs to repair the file, so it must run."""
    for handmade in ({"providers": ["local"]}, {"providers": "local"}, {"providers": 5},
                     {"providers": {"bad": "oops"}},
                     {"providers": {"e": {"kind": "openai", "api_key_env": {"x": KEYLIKE}}}},
                     {"providers": {"e": {"kind": "openai", "api_key_env": 5}}},
                     {"providers": {"u": {"kind": "openai", "base_url": [KEYLIKE]}}},
                     {"providers": {"u": {"kind": "openai", "base_url": "http://[::1"}}},
                     {"providers": {"h": {"kind": "openai", "headers": "x"}}},
                     {"providers": {"h": {"kind": "openai", "headers": {"a": [1], "b": None}}}}):
        cfg = {**DEFAULT_CONFIG, **handmade}
        assert cli.config_value(cfg, "batch.size", "3") == (["batch", "size"], 3), handmade
        assert cli.config_notes(cfg, ["batch", "size"], 3) == [], handmade
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    (tmp_path / "lx.config.json").write_text(
        json.dumps({"providers": {"bad": "oops", "u": {"kind": "openai", "base_url": [KEYLIKE]}}}),
        encoding="utf-8")
    r = _lx(["config", "set", "batch.size", "3"], tmp_path, env)
    assert r.returncode == 0 and "Traceback" not in _err(r), _both(r)
    assert _config(tmp_path)["batch"]["size"] == 3


@pytest.mark.parametrize("args", [
    ["config", "set", f"providers.{KEYLIKE}.kind", "openai"],
    ["config", "set", f"providers.{KEYLIKE}.timeout", "abc"],
    ["config", "set", f"providers.{KEYLIKE}.api_key_env.x", "y"],
    ["config", "set", f"providers.{KEYLIKE}", json.dumps({"kind": "openai"})],
    ["config", "set", "providers", json.dumps({KEYLIKE: {"kind": "openai"}})],
    ["config", "set", f"providers.x-{KEYLIKE}.kind", "openai"],
])
def test_a_new_provider_named_by_a_declared_credential_is_refused_before_anything_spells_it(
        tmp_path, args):
    """First in `config_value`: before `_addressable`, before any field rule's `{path}`.

    Measured at `6e6395a`: every sentence after that point spells the key it is
    about, so `providers.<key>.timeout abc` answered the key inside
    `_as_number`'s refusal. The spellings: the leaf, a deeper path, the block
    under the name, the block under `providers`, and a name that *contains*
    the key.
    """
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    before = (tmp_path / "lx.config.json").read_bytes()
    r = _lx(args, tmp_path, env)
    assert r.returncode == 2, (args, _both(r))
    assert _windows(KEYLIKE, _both(r)) == [], _both(r)
    assert _err(r).startswith("lx: a segment of the key being written — one the configuration does "
                              "not hold yet — is the content of OPENAI_API_KEY, which "
                              "providers.openai.api_key_env names as a key"), _err(r)
    assert (tmp_path / "lx.config.json").read_bytes() == before


def test_an_ordinary_new_provider_name_is_not_a_credential_whatever_its_shape(tmp_path):
    """No shape rule for names: long, hyphenated, dotted and placeholder-like names all write."""
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    for name in ("myproxy", "my-openai-compatible-gateway", "sk_gateway_2024", "lm-studio"):
        r = _lx(["config", "set", f"providers.{name}.kind", "openai"], tmp_path, env)
        assert r.returncode == 0, (name, _err(r))
    r = _lx(["config", "set", "providers", json.dumps({"a.b": {"kind": "openai"}})], tmp_path, env)
    assert r.returncode == 0, _err(r)
    # The predictable false positive, and its cheapest way out: another name.
    env = _project(tmp_path, _env(OPENAI_API_KEY="lm-studio"))
    r = _lx(["config", "set", "providers.lm-studio.kind", "openai"], tmp_path, env)
    assert r.returncode == 2 and "OPENAI_API_KEY" in _err(r), _both(r)
    r = _lx(["config", "set", "providers.lmstudio.kind", "openai"], tmp_path, env)
    assert r.returncode == 0, _err(r)


def test_a_block_that_declares_its_own_api_key_env_is_compared_against_that_variable(tmp_path):
    """`providers.gw '{"api_key_env": "GW_KEY", "model": <content of GW_KEY>}'`: the name is
    not in the merged configuration yet, and it is read out of the block being written."""
    env = _project(tmp_path, _env(GW_KEY=KEYLIKE))
    before = (tmp_path / "lx.config.json").read_bytes()
    r = _lx(["config", "set", "providers.gw",
             json.dumps({"kind": "openai", "api_key_env": "GW_KEY", "model": KEYLIKE})], tmp_path, env)
    assert r.returncode == 2, _both(r)
    assert _windows(KEYLIKE, _both(r)) == [], _both(r)
    assert "GW_KEY" in _err(r) and "same block" in _err(r), _err(r)
    assert (tmp_path / "lx.config.json").read_bytes() == before
    r = _lx(["config", "set", "providers.gw",
             json.dumps({"kind": "openai", "api_key_env": "GW_KEY", "model": "m"})], tmp_path, env)
    assert r.returncode == 0, _err(r)


def test_a_key_exported_under_an_undeclared_name_earns_a_note_and_is_stored(tmp_path):
    """The note, both triggers, on the terminal.

    A variable nothing declares whose *name* says it holds a credential, and
    then the same fact one step later, when its name is declared and the block
    already holds the content. Neither repeats the value — the `old → new` line
    above the note prints the model, by design; the note itself does not —
    and neither is a refusal. A variable whose name says nothing (`MODEL`)
    earns no line: `MODEL=gpt-4o-mini` is ordinary.
    """
    env = _project(tmp_path, _env(GROQ_API_KEY=KEYLIKE, MODEL="gpt-4o-mini"))
    r = _lx(["config", "set", "providers.groq.model", KEYLIKE], tmp_path, env)
    assert r.returncode == 0, _err(r)
    notes = [line for line in _out(r).splitlines() if line.startswith("note:")]
    assert notes == [
        "note: providers.groq.model now holds the content of the environment variable "
        "GROQ_API_KEY. Not an error — but if that is a key, it does not belong in "
        "lx.config.json: `lx config unset providers.groq.model`, and name GROQ_API_KEY in "
        "that provider's api_key_env instead."], _out(r)
    r = _lx(["config", "set", "providers.groq.model", "gpt-4o-mini"], tmp_path, env)
    assert r.returncode == 0 and "note:" not in _out(r), _out(r)
    r = _lx(["config", "set", "providers.groq.model", KEYLIKE], tmp_path, env)
    r = _lx(["routing", "set", "draft", f"groq:{KEYLIKE}"], tmp_path, env)
    # The leaf that matched, not the key that was addressed: the entry is the
    # object form here, and `lx config unset routing.draft.model` keeps the
    # provider half.
    assert r.returncode == 0 and "note: routing.draft.model now holds" in _out(r), _out(r)
    r = _lx(["config", "set", "providers.groq.api_key_env", "GROQ_API_KEY"], tmp_path, env)
    assert r.returncode == 0, _err(r)
    notes = [line for line in _out(r).splitlines() if line.startswith("note:")]
    assert notes == [
        "note: providers.groq.model already holds the content of GROQ_API_KEY, the variable "
        "providers.groq.api_key_env now names as this backend's key. Not an error — but if "
        "that is the key, it does not belong in lx.config.json: `lx config unset "
        "providers.groq.model`.",
        "note: routing.draft.model already holds the content of GROQ_API_KEY, the variable "
        "providers.groq.api_key_env now names as this backend's key. Not an error — but if "
        "that is the key, it does not belong in lx.config.json: `lx config unset "
        "routing.draft.model`."], _out(r)
    assert _windows(KEYLIKE, "\n".join(notes)) == []
    # Declared now: the next paste is refused, and the note is not printed twice.
    r = _lx(["config", "set", "providers.groq.model", KEYLIKE], tmp_path, env)
    assert r.returncode == 2 and "GROQ_API_KEY" in _err(r), _both(r)


@pytest.mark.parametrize("key", ["providers.openai.api_key", "providers.openai.apiKey",
                                 "providers.openai.token", "providers.openai.auth.token",
                                 "providers.openai.azure_api_key", "providers.openai.secret"])
def test_a_provider_field_named_like_a_credential_is_refused_by_name(tmp_path, key):
    """`headers`' rule for the fields beside it: nothing reads them, so a value there is a key."""
    env = _project(tmp_path, _env())
    before = (tmp_path / "lx.config.json").read_bytes()
    r = _lx(["config", "set", key, KEYLIKE], tmp_path, env)
    assert r.returncode == 2, (key, _both(r))
    assert _windows(KEYLIKE, _both(r)) == [] and "providers.openai.api_key_env" in _err(r), _err(r)
    assert (tmp_path / "lx.config.json").read_bytes() == before
    r = _lx(["config", "set", "providers.openai", json.dumps({key.split(".")[-1]: KEYLIKE})],
            tmp_path, env)
    assert r.returncode == 2 and _windows(KEYLIKE, _both(r)) == [], _both(r)
    for fine in ("providers.openai.top_p", "providers.openai.max_tokens", "providers.openai.api_key_env"):
        r = _lx(["config", "set", fine, "0.9" if fine.endswith("top_p") else
                 "4096" if fine.endswith("tokens") else "OPENAI_API_KEY"], tmp_path, env)
        assert r.returncode == 0, (fine, _err(r))


def test_extract_refuses_a_declared_credential_as_register_or_language_before_any_read(
        tmp_path, monkeypatch):
    """`--tone` and `--lang` land in the document row and in `.lx/tm.<lang>.jsonl`, which is tracked.

    Only the arguments are examined — a register already frozen on a row is
    never re-read against the rule — and the refusal is above every read, so a
    missing file is not what answers. `lx run` reaches the same function.
    """
    monkeypatch.setenv("OPENAI_API_KEY", MIXED)
    cfg = _sweep_cfg()
    for tone in (MIXED, f"  {MIXED}\n", f"Bearer {MIXED}"):
        with pytest.raises(ConfigError) as caught:
            cli.do_extract("nowhere.md", "zh-TW", cfg, tone=tone)
        assert _folded(MIXED, str(caught.value)) == [], str(caught.value)
        assert str(caught.value).startswith("the register (tone) was given the content of "
                                            "OPENAI_API_KEY, which providers.openai.api_key_env")
    with pytest.raises(ConfigError) as caught:
        cli.do_extract("nowhere.md", MIXED, cfg)
    assert str(caught.value).startswith("lang was given the content of OPENAI_API_KEY")
    with pytest.raises(FileNotFoundError):
        cli.do_extract("nowhere.md", "zh-TW", cfg, tone="literary")
    env = _project(tmp_path, _env(OPENAI_API_KEY=MIXED))
    (tmp_path / "d.md").write_bytes(b"The gate stood open.\n")
    for args in (["extract", "d.md", "--lang", "zh-TW", "--tone", MIXED],
                 ["extract", "d.md", "--lang", MIXED],
                 ["run", "d.md", "--lang", "zh-TW", "--tone", MIXED]):
        r = _lx(args, tmp_path, env)
        assert r.returncode == 2 and "Traceback" not in _err(r), (args, _both(r))
        assert _folded(MIXED, _both(r)) == [], _both(r)
    r = _lx(["status", "--json"], tmp_path, env)
    assert r.returncode == 0 and json.loads(_out(r))["projects"][0]["documents"] == [], _out(r)
    assert _folded(MIXED, _out(r)) == []


@pytest.mark.parametrize("reset, tone, says", [
    ("false", "technical", "`reset` is true or false — got text."),
    (1, "technical", "`reset` is true or false — got a number."),
    (True, {"a": 1}, "`tone` is a register name, as text — got a block."),
    (False, ["literary"], "`tone` is a register name, as text — got a list."),
])
def test_extract_type_checks_reset_and_tone_by_shape(reset, tone, says):
    """Divergence (28), closed: the string `"false"` was a reset that discarded a document."""
    with pytest.raises(ConfigError) as caught:
        cli.do_extract("nowhere.md", "zh-TW", _sweep_cfg(), tone=tone, reset=reset)
    assert str(caught.value) == f"{says} Nothing was written."


def test_glossary_set_refuses_a_declared_credential_in_any_field(tmp_path):
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    for args in (["glossary", "set", "Hello", KEYLIKE], ["glossary", "set", KEYLIKE, "哈囉"],
                 ["glossary", "set", "Hello", "哈囉", "--forbidden", KEYLIKE]):
        r = _lx(args, tmp_path, env)
        assert r.returncode == 2 and _windows(KEYLIKE, _both(r)) == [], (args, _both(r))
    r = _lx(["glossary", "set", "Hello", "哈囉"], tmp_path, env)
    assert r.returncode == 0, _err(r)
    assert "PASTED" not in (tmp_path / "config" / "glossary.csv").read_text(encoding="utf-8")


def test_checked_mode_names_the_stages_and_never_the_value():
    from scriptorium.cli import UnusableTarget
    assert cli.checked_mode("draft") == "draft"
    for mode, shape in (("audit", "text that is none of them"), (KEYLIKE, "text that is none of them"),
                        (5, "a number"), (None, "null"), (["draft"], "a list")):
        with pytest.raises(UnusableTarget) as caught:
            cli.checked_mode(mode)
        assert str(caught.value) == f"`mode` is one of draft, polish, repair — this request sent {shape}."
        assert _windows(KEYLIKE, str(caught.value)) == []


# ── HANDOFF-080, second round: what the security-tier re-derivation found ──

#: A key of 104 characters, the shape of a hosted key: too long for a language
#: tag by shape, so `language_tag` refuses it before the credential rule sees it.
LONG_KEY = "sk-proj-" + "A1b2C3d4" * 12

#: A 13-character declared secret: under `_ENV_LONG`, so equality is the only
#: arm that can catch it, and `_leaves`' strip is what makes equality reach a
#: padded paste on the paths no field rule strips.
SHORT = "S3CRETVALUE9X"


def test_a_declared_name_is_read_by_the_files_spelling_and_folded_on_windows(monkeypatch):
    """On Windows `os.environ` upper-cases every name, `_field_api_key_env` accepts a
    mixed-case spelling anyway and the transport reads the key through it — so a rule
    that iterated the environment's keys and tested the file's spelling declared nothing
    there, silently. Found by the security-tier re-derivation; held on both platforms.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("Groq_Key", KEYLIKE)
    cfg = {**DEFAULT_CONFIG, "providers": {
        **DEFAULT_CONFIG["providers"],
        "groq": {"kind": "openai", "model": KEYLIKE, "api_key_env": "Groq_Key"}}}
    with pytest.raises(ConfigError) as caught:
        cli.config_value(cfg, "providers.groq.model", KEYLIKE)
    assert "Groq_Key" in str(caught.value) and _windows(KEYLIKE, str(caught.value)) == []
    assert cli.config_notes(cfg, ["providers", "groq", "model"], KEYLIKE) == []
    # The second trigger folds the same way: naming the variable while the
    # block already holds its content earns the note, not silence.
    before = {**cfg, "providers": {**cfg["providers"],
                                   "groq": {"kind": "openai", "model": KEYLIKE}}}
    notes = cli.config_notes(before, ["providers", "groq", "api_key_env"], "Groq_Key")
    assert len(notes) == 1 and "providers.groq.model already holds" in notes[0], notes
    if os.name == "nt":
        # The environment's own spelling differs from the file's: one variable here.
        monkeypatch.setenv("GROQ_KEY2", KEYLIKE)
        cfg["providers"]["groq"]["api_key_env"] = "groq_key2"
        with pytest.raises(ConfigError):
            cli.config_value(cfg, "providers.groq.model", KEYLIKE)


def test_language_tag_repeats_nothing_it_refuses():
    """A key longer than a tag may be was refused here, whole, before the credential rule."""
    from scriptorium.cli import UnsafePath
    with pytest.raises(UnsafePath) as caught:
        cli.language_tag(LONG_KEY)
    assert _windows(LONG_KEY, str(caught.value)) == [], str(caught.value)
    assert str(caught.value).startswith("lang is not a language tag — got text.")
    with pytest.raises(UnsafePath) as caught:
        cli.language_tag(5, field="lang")
    assert "got a number" in str(caught.value)
    assert cli.language_tag("zh-TW") == "zh-TW"


@pytest.mark.parametrize("args", [
    ["config", "set", f"providers.{KEYLIKE}.api_key_env", "GW_KEY"],
    ["config", "set", f"providers.{KEYLIKE}", json.dumps({"kind": "openai", "api_key_env": "GW_KEY"})],
    ["config", "set", "providers", json.dumps({KEYLIKE: {"kind": "openai", "api_key_env": "GW_KEY"}})],
])
def test_a_new_name_that_is_the_content_of_the_variable_the_same_write_declares_is_refused(
        tmp_path, args):
    """The name check reads the write's own declaration, as the value check does."""
    env = _project(tmp_path, _env(GW_KEY=KEYLIKE))
    before = (tmp_path / "lx.config.json").read_bytes()
    r = _lx(args, tmp_path, env)
    assert r.returncode == 2, (args, _both(r))
    assert _windows(KEYLIKE, _both(r)) == [] and "GW_KEY" in _err(r), _both(r)
    assert (tmp_path / "lx.config.json").read_bytes() == before
    r = _lx(["config", "set", "providers.gw.api_key_env", "GW_KEY"], tmp_path, env)
    assert r.returncode == 0, _err(r)


@pytest.mark.parametrize("args", [
    ["config", "set", KEYLIKE, "1"],
    ["config", "set", f"formats.map.{KEYLIKE}", "text"],
    ["config", "set", f"routing.{KEYLIKE}", "openai"],
    ["config", "set", f"providers.{KEYLIKE}.", "x"],
    ["config", "set", f"providers.{KEYLIKE}..kind", "openai"],
    ["config", "unset", f"routing.{KEYLIKE}"],
    ["config", "unset", f"providers.{KEYLIKE}.model"],
])
def test_a_declared_credential_in_any_key_position_is_refused_before_anything_spells_it(
        tmp_path, args):
    """Not the provider position alone: the stage position, the top level, a block's key,
    and a key `split_key` would otherwise quote whole for its empty segment."""
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    before = (tmp_path / "lx.config.json").read_bytes()
    r = _lx(args, tmp_path, env)
    assert r.returncode == 2, (args, _both(r))
    assert _windows(KEYLIKE, _both(r)) == [] and "OPENAI_API_KEY" in _err(r), _both(r)
    assert (tmp_path / "lx.config.json").read_bytes() == before


def test_a_stage_that_is_not_one_is_refused_without_being_repeated(tmp_path):
    env = _project(tmp_path, _env())
    for args in (["config", "set", "routing.audit", "openai"], ["routing", "set", "draft", "x"]):
        r = _lx(args, tmp_path, env)
        assert r.returncode == 2, (args, _both(r))
    r = _lx(["config", "set", "routing.audit", "openai"], tmp_path, env)
    assert "audit" not in _err(r) and "draft, polish, repair" in _err(r), _err(r)
    with pytest.raises(ConfigError) as caught:
        cli.do_routing_set(DEFAULT_CONFIG, "audit", "openai", str(tmp_path / "lx.config.json"))
    assert "audit" not in str(caught.value) and "draft, polish, repair" in str(caught.value)


def test_do_translate_refuses_a_mode_that_is_not_a_stage_before_it_reads_anything():
    """`checked_mode` sits in `do_translate` as well as in `do_select`; a mutant that dropped
    the second survived every test, and the failure signature is a `FileNotFoundError`."""
    from scriptorium.cli import UnusableTarget
    with pytest.raises(UnusableTarget):
        cli.do_translate("nowhere.md", "zh-TW", _sweep_cfg(), [{"id": "s0001"}], "audit")


def test_a_declared_credential_is_not_a_model_for_a_run_either(tmp_path, monkeypatch):
    """`--model` on the terminal and `model` in `do_translate`: the dry run printed it."""
    monkeypatch.setenv("OPENAI_API_KEY", KEYLIKE)
    with pytest.raises(ConfigError) as caught:
        cli.do_translate("nowhere.md", "zh-TW", _sweep_cfg(), [{"id": "s0001"}], "draft",
                         model=KEYLIKE)
    assert _windows(KEYLIKE, str(caught.value)) == [] and str(caught.value).startswith("model was given")
    env = _project(tmp_path, _env(OPENAI_API_KEY=KEYLIKE))
    (tmp_path / "d.md").write_bytes(b"The gate stood open.\n")
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    r = _lx(["translate", "d.md", "--lang", "zh-TW", "--model", KEYLIKE, "--dry-run"], tmp_path, env)
    assert r.returncode == 2 and _windows(KEYLIKE, _both(r)) == [], _both(r)
    r = _lx(["translate", "d.md", "--lang", "zh-TW", "--model", "gpt-4o-mini", "--dry-run"],
            tmp_path, env)
    assert r.returncode == 0 and "model=gpt-4o-mini" in _out(r), _both(r)


def test_an_existing_provider_named_like_a_placeholder_is_still_writable(tmp_path):
    """The red-line direction of the name rule: an existing name is a key of the file."""
    env = _project(tmp_path, _env(OPENAI_API_KEY="lm-studio"))
    (tmp_path / "lx.config.json").write_text(
        json.dumps({"providers": {"lm-studio": {"kind": "openai", "model": "m"}}}), encoding="utf-8")
    for args in (["config", "set", "providers.lm-studio.model", "x"],
                 ["config", "set", "providers.lm-studio.timeout", "9"],
                 ["config", "unset", "providers.lm-studio.timeout"]):
        r = _lx(args, tmp_path, env)
        assert r.returncode == 0, (args, _both(r))


def test_the_residual_holds_in_the_block_spelling_and_earns_no_note(tmp_path):
    """`MY_GATEWAY_TOKEN=MY_GATEWAY_TOKEN`, sixteen characters: above the floor, and still a name."""
    env = _project(tmp_path, _env(MY_GATEWAY_TOKEN="MY_GATEWAY_TOKEN"))
    r = _lx(["config", "set", "providers.gw",
             json.dumps({"kind": "openai", "api_key_env": "MY_GATEWAY_TOKEN"})], tmp_path, env)
    assert r.returncode == 0 and "note:" not in _out(r), _both(r)
    r = _lx(["config", "set", "providers.gw.api_key_env", "MY_GATEWAY_TOKEN"], tmp_path, env)
    assert r.returncode == 0 and "note:" not in _out(r), _both(r)


@pytest.mark.parametrize("scheme", ["Bearer", "Basic", "Token", "bearer"])
def test_every_authorization_scheme_gives_up_its_token(scheme, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = {**DEFAULT_CONFIG, "providers": {
        **DEFAULT_CONFIG["providers"], "p": {"kind": "openai", "model": "m"},
        "gw": {"kind": "openai", "headers": {"Authorization": f"{scheme} {SHORT}"}}}}
    with pytest.raises(ConfigError) as caught:
        cli.config_value(cfg, "providers.p.model", SHORT)
    assert "providers.gw.headers" in str(caught.value) and SHORT not in str(caught.value)


def test_a_password_holding_a_bare_at_sign_is_declared_whole(monkeypatch):
    """`rpartition("@")`, as `Provider._userinfo` reads it: the last `@` ends the userinfo."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = {**DEFAULT_CONFIG, "providers": {
        **DEFAULT_CONFIG["providers"], "p": {"kind": "openai", "model": "m"},
        "gw": {"kind": "openai", "base_url": f"http://u:p@ss{SHORT}@127.0.0.1:9/v1"}}}
    assert len(cli._declared_credentials(cfg)) == 4
    with pytest.raises(ConfigError) as caught:
        cli.config_value(cfg, "providers.p.model", f"p@ss{SHORT}")
    assert SHORT not in str(caught.value)


def test_a_padded_paste_of_a_short_declared_secret_is_refused_where_no_rule_strips(monkeypatch):
    """`_leaves`' own strip decides on a rule-less key, in a list, and at extract."""
    monkeypatch.setenv("OPENAI_API_KEY", SHORT)
    cfg = _sweep_cfg()
    for key, raw in (("targets", f"  {SHORT}  "), ("targets", json.dumps([f"  {SHORT}\n"])),
                     ("handmade", f"\t{SHORT}")):
        with pytest.raises(ConfigError) as caught:
            cli.config_value(cfg, key, raw)
        assert str(caught.value).startswith(f"{key} was given the content of OPENAI_API_KEY"), (key, raw)
    for kw in ({"tone": f"  {SHORT}\n"}, {"lang": f" {SHORT}"}):
        with pytest.raises(ConfigError):
            cli.do_extract("nowhere.md", kw.get("lang", "zh-TW"), cfg, tone=kw.get("tone"))


def test_containment_opens_at_exactly_twenty_characters(monkeypatch):
    twenty, nineteen = "K" * 20, "K" * 19
    cfg = _sweep_cfg()
    monkeypatch.setenv("OPENAI_API_KEY", twenty)
    with pytest.raises(ConfigError):
        cli.config_value(cfg, "providers.p.model", f"Bearer {twenty}")
    monkeypatch.setenv("OPENAI_API_KEY", nineteen)
    assert cli.config_value(cfg, "providers.p.model", f"Bearer {nineteen}")[1] == f"Bearer {nineteen}"
    with pytest.raises(ConfigError):
        cli.config_value(cfg, "providers.p.model", nineteen)


def test_a_block_under_providers_declaring_a_nested_api_key_env_is_compared(tmp_path):
    env = _project(tmp_path, _env(GW_KEY=KEYLIKE))
    r = _lx(["config", "set", "providers",
             json.dumps({"gw": {"kind": "openai", "api_key_env": "GW_KEY", "model": KEYLIKE}})],
            tmp_path, env)
    assert r.returncode == 2 and _windows(KEYLIKE, _both(r)) == [] and "GW_KEY" in _err(r), _both(r)


@pytest.mark.parametrize("key", ["providers.openai.extra.api_key", "providers.openai.extra.token"])
def test_a_credential_named_field_is_refused_at_any_depth(tmp_path, key):
    env = _project(tmp_path, _env())
    r = _lx(["config", "set", key, KEYLIKE], tmp_path, env)
    assert r.returncode == 2 and _windows(KEYLIKE, _both(r)) == [], (key, _both(r))
    r = _lx(["config", "set", "providers.openai", json.dumps({"extra": {key.split(".")[-1]: KEYLIKE}})],
            tmp_path, env)
    assert r.returncode == 2 and _windows(KEYLIKE, _both(r)) == [], _both(r)
    r = _lx(["config", "set", "providers.openai.extra.thing", "x"], tmp_path, env)
    assert r.returncode == 0, _err(r)


def test_the_in_process_refusals_print_nothing_either(monkeypatch, capsys):
    """A leak through stdout beside the raise was seen by subprocess tests only."""
    monkeypatch.setenv("OPENAI_API_KEY", KEYLIKE)
    cfg = _sweep_cfg()
    for key, raw in (("providers.p.model", KEYLIKE), ("tone", KEYLIKE),
                     ("routing.draft", f"p:{KEYLIKE}"), (f"providers.{KEYLIKE}.kind", "openai")):
        with pytest.raises(ConfigError):
            cli.config_value(cfg, key, raw)
    with pytest.raises(ConfigError):
        cli.do_extract("nowhere.md", "zh-TW", cfg, tone=KEYLIKE)
    captured = capsys.readouterr()
    assert _windows(KEYLIKE, captured.out + captured.err) == [], captured


def test_glossary_set_repeats_no_severity_and_refuses_a_credential_there(tmp_path):
    from scriptorium.config import load_config
    _project(tmp_path, _env())
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        cfg = load_config()
        with pytest.raises(ConfigError) as caught:
            cli.do_glossary_set(cfg, "Hello", "哈囉", severity=KEYLIKE)
        assert _windows(KEYLIKE, str(caught.value)) == [] and "got text" in str(caught.value)
    finally:
        os.chdir(cwd)


def test_the_first_note_names_the_leaf_that_matched_on_a_block_write(tmp_path):
    env = _project(tmp_path, _env(GROQ_API_KEY=KEYLIKE))
    r = _lx(["config", "set", "providers.gw", json.dumps({"kind": "openai", "model": KEYLIKE})],
            tmp_path, env)
    assert r.returncode == 0, _err(r)
    notes = [line for line in _out(r).splitlines() if line.startswith("note:")]
    assert len(notes) == 1 and notes[0].startswith("note: providers.gw.model now holds"), _out(r)
    assert "`lx config unset providers.gw.model`" in notes[0]
