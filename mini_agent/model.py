from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelClient:
    api_key: str
    base_url: str
    model: str
    timeout: float = 90
    prompt_cache_key: str | None = None

    def __post_init__(self) -> None:
        # Match session IDs without coupling the HTTP adapter to session storage.
        if self.prompt_cache_key is not None and (
            not isinstance(self.prompt_cache_key, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.prompt_cache_key) is None
        ):
            raise ValueError(
                "prompt_cache_key must contain 1-64 ASCII letters, digits, underscores or hyphens"
            )

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.prompt_cache_key is not None:
            body["prompt_cache_key"] = self.prompt_cache_key
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read(512).decode("utf-8", "replace")
            hint = ""
            if self.prompt_cache_key is not None and (
                exc.code in {400, 422} or "prompt_cache_key" in detail.lower()
            ):
                hint = (
                    " If the endpoint rejects prompt_cache_key, "
                    "run again with --no-prompt-cache to disable it."
                )
            raise RuntimeError(f"model API error {exc.code}: {detail}{hint}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"model API connection failed: {exc}") from exc
