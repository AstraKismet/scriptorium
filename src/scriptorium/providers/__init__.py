"""Backend registry."""

import os

from ..config import printable_url
from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .openai_compat import OpenAICompatProvider

KINDS = {
    "openai": OpenAICompatProvider,
    "openai-compatible": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}

__all__ = ["Provider", "ProviderError", "build", "available"]


def build(name, cfg, model=None):
    """Instantiate the named provider from ``lx.config.json``.

    ``model`` overrides the spec's own. It is how a stage routed to
    ``{"provider": "local", "model": …}`` reaches a different model at the same
    endpoint without a duplicate provider entry whose `base_url`, `api_key_env`
    and timeout are copies that drift. Which model that is has already been
    decided by `config.resolve_route`, most specific first; this only applies it,
    and only to a copy — the caller's `cfg` is never mutated, because one run
    overriding a model must not change what the next one resolves.
    """
    specs = cfg.get("providers", {})
    if name not in specs:
        raise ProviderError(
            f"unknown provider {name!r}. Configured: {', '.join(sorted(specs)) or 'none'}")
    spec = specs[name]
    if model and model != spec.get("model"):
        spec = {**spec, "model": model}
    kind = spec.get("kind", "openai")
    if kind not in KINDS:
        raise ProviderError(f"unknown provider kind {kind!r}; expected one of {sorted(KINDS)}")
    return KINDS[kind](name, spec)


def _summary(name, spec):
    """One provider row, and a sentence instead wherever the spec cannot be read.

    Reported rather than raised, the way `web/server._stage_route` already
    reports a malformed routing entry — and *per entry*, so one bad block does
    not cost the caller the other three. What made this worth changing is which
    caller: `/api/state` draws the whole page, and it was answering `400` with
    nothing in it, on a configuration the person had no way to repair except by
    opening the file. Now that configuration is writable over HTTP they do have
    a way, and it runs through this same projection — so a total `available()` is
    the precondition for the endpoint that fixes the problem, not a courtesy.

    `error` is present only when there is one, exactly as the routing-stage shape
    beside it does it. Every other key is always there and holds a value of the
    documented type, so a client can render the row without testing each field.

    **A spec whose credential field cannot be read never reports "no key
    needed".** `needs_key: False` with `key_present: True` is how a local runtime
    that wants no credential looks, and folding an unreadable `api_key_env` to
    `""` would produce exactly that pair — a green light nobody earned. It is red
    instead. A *readable* `api_key_env` keeps its honest answer even when another
    field of the same spec is broken, because whether a key is wanted is not made
    untrue by a malformed `model`.
    """
    row = {"name": name, "kind": "", "model": "", "base_url": "",
           "needs_key": True, "key_present": False, "key_env": ""}
    if not isinstance(spec, dict):
        row["error"] = (f"providers.{name} is a block of settings — `kind`, `base_url`, "
                        f"`model` and so on — and this one holds a single value.")
        return row
    problems = []
    # `kind` defaults to `openai` and the other two to empty, which is what
    # `build()` reads — so an explicit `"kind": ""` stays empty here and is
    # refused there, rather than being projected as a backend nobody configured.
    for field, absent in (("kind", "openai"), ("model", ""), ("base_url", "")):
        value = spec.get(field, absent)
        if isinstance(value, str):
            row[field] = printable_url(value) if field == "base_url" else value
        else:
            # Not an error the row can carry a value for: the contract documents
            # all three as strings, and echoing a number here would put a value
            # outside the documented type on the wire rather than saying so.
            problems.append(f"`{field}` is text")
    env = spec.get("api_key_env") or ""
    if isinstance(env, str):
        row["key_env"] = env
        row["needs_key"] = bool(env)
        row["key_present"] = bool(os.environ.get(env)) if env else True
    else:
        # `os.environ.get` raises `TypeError: str expected` on a non-string, which
        # is how this function used to take the whole endpoint down over one
        # hand-edited field.
        problems.append("`api_key_env` is the NAME of an environment variable, as text")
    if not problems:
        return row
    row["error"] = (f"providers.{name} cannot be read: "
                    f"{', and '.join(problems)}. Fix it in lx.config.json.")
    return row


def available(cfg):
    """Provider summaries for the CLI and the web UI, with credential status.

    `base_url` goes through `config.printable_url`, so a userinfo or `?key=`
    fragment a hand-edited file carries is not printed by `lx providers` and not
    served to the browser by `/api/state`. `lx config set` refuses to write
    either, but this is the summary a person reads when something is wrong — and
    it was showing in full what `lx config get` had just masked.

    A `providers` value that is not a block at all lists nothing. There is no row
    to hang the sentence on, and `config.resolve_route` refuses the same shape by
    name, so every stage of the routing projection beside this one carries the
    explanation. Empty here and three sentences there beats a synthetic row.
    """
    specs = cfg.get("providers") or {}
    if not isinstance(specs, dict):
        return []
    return [_summary(name, specs[name]) for name in sorted(specs, key=str)]
