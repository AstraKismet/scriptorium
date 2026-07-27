"""Anthropic Messages API."""

from .base import Provider, ProviderError


class AnthropicProvider(Provider):
    kind = "anthropic"

    def complete(self, system, user):
        base = self.spec.get("base_url", "https://api.anthropic.com").rstrip("/")
        url = f"{base}/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        key = self.api_key
        if not key:
            raise ProviderError(
                f"{self.name}: no API key. Set the environment variable named in "
                f"providers.{self.name}.api_key_env.")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": self.spec.get("anthropic_version", "2023-06-01"),
        }
        data = self._post(url, payload, headers)
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise ProviderError(f"{self.name}: empty completion: {str(data)[:300]}")
        return text
