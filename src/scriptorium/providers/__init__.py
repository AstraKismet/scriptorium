"""Backend registry."""

from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .openai_compat import OpenAICompatProvider

KINDS = {
    "openai": OpenAICompatProvider,
    "openai-compatible": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}

__all__ = ["Provider", "ProviderError", "build", "available"]


def build(name, cfg):
    """Instantiate the named provider from ``lx.config.json``."""
    specs = cfg.get("providers", {})
    if name not in specs:
        raise ProviderError(
            f"unknown provider {name!r}. Configured: {', '.join(sorted(specs)) or 'none'}")
    spec = specs[name]
    kind = spec.get("kind", "openai")
    if kind not in KINDS:
        raise ProviderError(f"unknown provider kind {kind!r}; expected one of {sorted(KINDS)}")
    return KINDS[kind](name, spec)


def available(cfg):
    """Provider summaries for the CLI and the web UI, with credential status."""
    import os
    out = []
    for name, spec in sorted(cfg.get("providers", {}).items()):
        env = spec.get("api_key_env") or ""
        out.append({
            "name": name,
            "kind": spec.get("kind", "openai"),
            "model": spec.get("model", ""),
            "base_url": spec.get("base_url", ""),
            "needs_key": bool(env),
            "key_present": bool(os.environ.get(env)) if env else True,
            "key_env": env,
        })
    return out
