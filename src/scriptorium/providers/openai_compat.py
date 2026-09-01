"""OpenAI-compatible chat completions.

Deliberately conservative so that self-hosted servers work unmodified:

* no ``response_format`` unless the project opts in — many local runtimes reject
  unknown fields outright rather than ignoring them
* no streaming, no tool calls, no system-role assumptions beyond the basics
* ``Authorization`` sent only when a key is actually present, since local
  servers usually want no auth header at all

The shape is written against Ollama, LM Studio, llama.cpp's ``llama-server``,
vLLM, LiteLLM and OpenAI itself. Only one of those is a standing *measurement*:
a live round trip through this class against ``llama-server`` build
``b9892-ee445f93d`` in router mode, on 2026-08-20, translating a document end to
end to a green ``lx check``. The rest is read from their documentation, which is
a weaker claim and is spelled that way on purpose — the sentence this replaces
said "verified" of all six, and five of them had never been run.
"""

from ..config import printable_url
from .base import Provider, ProviderError, _tame


class OpenAICompatProvider(Provider):
    kind = "openai"

    def list_models(self):
        """``GET {base_url}/models`` — what this endpoint says it serves.

        Two shapes are read, because the servers this project targets do not
        agree. OpenAI's own is ``{"data": [{"id": …}]}`` and everything
        OpenAI-compatible follows it. llama.cpp's router puts a per-model
        ``status`` beside the id, and its value is an **object**, not a string:
        measured against build ``b9892-ee445f93d``, ``status`` is
        ``{"value": "sleeping", "args": [...], "preset": "..."}``. Reading it as
        a string is the mistake worth naming — the reader wants `value`, and
        `args` is the whole `llama-server` argv, which carries absolute paths off
        the operator's disk and is not ours to print. `Provider._listing` holds
        both of those, so the two backends cannot come to disagree.
        """
        base = self.spec.get("base_url", "http://localhost:11434/v1").rstrip("/")
        headers = {}
        key = self.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = self._get(f"{base}/models", headers)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            # `printable_url`, not `base`. This is a display surface — it reaches
            # stderr through `cli.main`'s exit-2 tuple, which this same change put
            # `ProviderError` into — and invariant 6 says every one of them shares
            # one answer about what is printable over a `base_url`. It did not,
            # and the adversarial pass over this work found it: a hand-edited
            # `http://h/v1?key=SECRET` was masked by `lx providers` and printed in
            # full by `lx models` beside it. That is the *same* defect closed for
            # `describe()` on 2026-08-13, reintroduced by a new surface — which is
            # exactly what AGENTS.md means by "the enumerated list is a symptom of
            # the rule and never its definition".
            raise ProviderError(
                f"{self.name}: {printable_url(base)}/models did not answer an OpenAI "
                f"model list (expected a `data` array): {_tame(str(data)[:300])}")
        return self._listing(rows)

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
        # `not content.strip()`, not `content is None`. An empty string and a
        # string of spaces are both a model that produced nothing, and both used
        # to be returned as success — measured 2026-08-20, `complete()` returned
        # `''` with no error. The failure then surfaced two hops downstream as
        # `no JSON object in reply: ''` from `parse_reply`, which reads as a
        # protocol fault and sends the reader to look at the prompt.
        #
        # `providers/anthropic.py` has refused this since it was written; this is
        # the two backends agreeing, not a new policy. It also puts the refusal at
        # the transport, where the three that already exist can see it:
        # `translate.accept` refuses an empty *proposal*, `cli.do_apply` refuses
        # an empty *target* at the door, and now nothing empty is even offered.
        #
        # The `isinstance` guard is not decoration. `content` is whatever the
        # backend put there, and a gateway answering the newer content-parts
        # shape puts a *list*. Before this, that list was returned unchanged and
        # failed in `parse_reply`; with a bare `.strip()` it would fail here with
        # an `AttributeError`, which is worse. It joins the shape error above,
        # where it belongs.
        if not isinstance(content, str):
            raise ProviderError(
                f"{self.name}: unexpected response shape: message.content is "
                f"{type(content).__name__}, not text: {str(data)[:300]}")
        if not content.strip():
            raise ProviderError(
                f"{self.name}: empty completion — the model returned no text. "
                "It may have hit max_tokens, or stopped on its first token; raise "
                "`max_tokens` or lower `batch.size` if it recurs.")
        return content
