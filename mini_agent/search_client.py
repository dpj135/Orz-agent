"""Minimal Tavily HTTP adapter; returned web content is untrusted data."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

SEARCH_ENDPOINT = "https://api.tavily.com/search"
SEARCH_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 524_288
MAX_QUERY_CHARACTERS = 1000
MAX_RESULTS = 10
DEFAULT_RESULTS = 5
MAX_SNIPPET_BYTES = 2000
MAX_TITLE_BYTES = 300
MAX_URL_CHARACTERS = 4096


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent authenticated search requests from being redirected."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects before forwarding credentials.

        Args:
            req: Original request.
            fp: Response stream.
            code: Redirect status.
            msg: Status message.
            headers: Response headers.
            newurl: Untrusted redirect destination.

        Returns:
            None; urllib reports the redirect as an HTTP error.
        """
        return None


@dataclass(frozen=True)
class SearchClient:
    """Search a fixed trusted endpoint without exposing credentials in repr."""

    api_key: str = field(repr=False)

    def search(self, query: str, max_results: int, budget: int) -> dict[str, Any]:
        """Request basic retrieval and normalize bounded structured sources.

        Args:
            query: Validated search query, never a conversation or workspace dump.
            max_results: Validated number of requested sources.
            budget: Maximum serialized bytes for the results array.

        Returns:
            Provider, normalized results and truncation status; failures are sanitized.
        """
        if not self.api_key.strip():
            raise ValueError("TAVILY_API_KEY is not configured")
        body = json.dumps(
            {
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            SEARCH_ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=SEARCH_TIMEOUT_SECONDS
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise RuntimeError(f"search API failed (HTTP {exc.code})") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise RuntimeError("search network request failed") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("search response exceeds byte limit")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("search API returned invalid JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("search API returned invalid results")
        results = []
        seen = set()
        truncated = False
        for item in data["results"]:
            if not isinstance(item, dict):
                raise ValueError("search API returned invalid source")
            url = item.get("url")
            if not isinstance(url, str) or len(url) > MAX_URL_CHARACTERS:
                truncated = True
                continue
            try:
                parsed = urllib.parse.urlsplit(url)
                valid = parsed.scheme in {"http", "https"} and parsed.hostname
                valid = valid and not parsed.username and not parsed.password
            except ValueError:
                valid = False
            if not valid or any(ord(c) < 32 for c in url):
                truncated = True
                continue
            if url in seen:
                continue
            seen.add(url)
            source = {"url": url}
            for key, remote, cap in [
                ("title", "title", MAX_TITLE_BYTES),
                ("snippet", "content", MAX_SNIPPET_BYTES),
            ]:
                text = item.get(remote) or ""
                if not isinstance(text, str):
                    raise ValueError("search API returned invalid source text")
                encoded = text.encode("utf-8", "replace")
                truncated |= len(encoded) > cap
                source[key] = encoded[:cap].decode("utf-8", "ignore")
            if len(results) >= max_results or len(
                json.dumps([*results, source], ensure_ascii=False).encode("utf-8")
            ) > max(0, budget):
                truncated = True
                break
            results.append(source)
        return {"ok": True, "provider": "tavily", "results": results, "truncated": truncated}
