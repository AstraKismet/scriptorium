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

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import cli  # noqa: E402
from scriptorium.config import (  # noqa: E402
    DEFAULT_CONFIG,
    MISSING,
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
    assert secret not in _both(r)
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
    """`_translate` reads `args.model`; a subcommand that forgot the flag would crash.

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
