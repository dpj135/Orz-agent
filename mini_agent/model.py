from __future__ import annotations

import json
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

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {"model": self.model, "messages": messages}
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
            raise RuntimeError(f"model API error {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"model API connection failed: {exc}") from exc
