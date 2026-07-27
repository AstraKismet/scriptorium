"""OpenAI-compatible chat completions.

Deliberately conservative so that self-hosted servers work unmodified:

* no ``response_format`` unless the project opts in — many local runtimes reject
  unknown fields outright rather than ignoring them
* no streaming, no tool calls, no system-role assumptions beyond the basics
* ``Authorization`` sent only when a key is actually present, since local
  servers usually want no auth header at all

Verified shape against Ollama, LM Studio, llama.cpp server, vLLM, LiteLLM, and
OpenAI itself.
"""

from .base import Provider, ProviderError


class OpenAICompatProvider(Provider):
    kind = "openai"

    def complete(self, system, user):
        base = self.spec.get("base_url", "http://localhost:11434/v1").rstrip("/")
        url = f"{base}/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.spec.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        for key in ("top_p", "seed", "stop", "presence_penalty", "frequency_penalty"):
            if key in self.spec:
                payload[key] = self.spec[key]

        headers = {"Content-Type": "application/json"}
        key = self.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"

        data = self._post(url, payload, headers)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(
                f"{self.name}: unexpected response shape: {str(data)[:300]}") from e
        if content is None:
            raise ProviderError(f"{self.name}: empty completion (model may have hit max_tokens)")
        return content
