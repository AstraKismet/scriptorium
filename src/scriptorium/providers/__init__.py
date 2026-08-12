"""Backend registry."""

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


def available(cfg):
    """Provider summaries for the CLI and the web UI, with credential status.

    `base_url` goes through `config.printable_url`, so a userinfo or `?key=`
    fragment a hand-edited file carries is not printed by `lx providers` and not
    served to the browser by `/api/state`. `lx config set` refuses to write
    either, but this is the summary a person reads when something is wrong — and
    it was showing in full what `lx config get` had just masked.
    """
    import os
    out = []
    for name, spec in sorted(cfg.get("providers", {}).items()):
        env = spec.get("api_key_env") or ""
        out.append({
            "name": name,
            "kind": spec.get("kind", "openai"),
            "model": spec.get("model", ""),
            "base_url": printable_url(spec.get("base_url", "")),
            "needs_key": bool(env),
            "key_present": bool(os.environ.get(env)) if env else True,
            "key_env": env,
        })
    return out
