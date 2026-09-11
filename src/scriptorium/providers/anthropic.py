"""Anthropic Messages API."""

from ..config import printable_url
from .base import Provider


class AnthropicProvider(Provider):
    kind = "anthropic"

    #: The Messages API spells the two counts differently and publishes no
    #: total. `Provider` computes the total from these two on every backend, so
    #: there is nothing else to override — see `Provider.USAGE_FIELDS`.
    USAGE_FIELDS = ("input_tokens", "output_tokens")

    def list_models(self):
        """``GET {base_url}/v1/models`` — the Messages API's own listing.

        The key headers differ from `complete`'s only in that there is no body.
        A key is required here for the same reason it is there: this endpoint is
        authenticated, and a listing that answered 401 with a stack trace would
        be a worse answer than the sentence below.

        It goes through `Provider._listing` rather than building its own list, so
        that the untrusted-reply rules hold on both backends. They did not for a
        day: the control-character filter was written private to
        `openai_compat.py`, and a `kind: "anthropic"` `base_url` is configurable
        — LiteLLM serves the Messages API — so a listing that forged terminal
        rows was reachable through this class alone. Found by the adversarial
        pass over the change that introduced it.
        """
        base = self.spec.get("base_url", "https://api.anthropic.com").rstrip("/")
        key = self.api_key
        if not key:
            raise self._refusal(
                f"{self.name}: no API key. Set the environment variable named in "
                f"providers.{self.name}.api_key_env.")
        data = self._get(f"{base}/v1/models", {
            "x-api-key": key,
            "anthropic-version": self.spec.get("anthropic_version", "2023-06-01"),
        })
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            # `printable_url` and `_excerpt`; see the twin of this message in
            # `openai_compat`. This one stood untamed as well as uncut-after-
            # redaction until 2026-09-11 — the day a rule about an untrusted
            # reply was again written private to the other backend first.
            raise self._refusal(
                f"{self.name}: {printable_url(base)}/v1/models did not answer a model "
                f"list (expected a `data` array): {self._excerpt(str(data), 300)}")
        return self._listing(rows)

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
            raise self._refusal(
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
            raise self._refusal(
                f"{self.name}: empty completion: {self._excerpt(str(data), 300)}")
        return text
