"""Routed HTTP fake for pipeline tests: canned bodies chosen by URL and params.

A route key is a URL substring, optionally followed by ``|k=v`` segments that
must all equal the request's query parameters, for example
``"api.stlouisfed.org|series_id=CPIAUCSL"``. When several routes match, the one
with the most parameter constraints wins, then the longest URL substring, so a
specific route can override a catch-all for the same host.

A request no route matches raises :class:`UnroutedRequest` instead of reaching
the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode

from src.ingestion.base import HttpResponse


class UnroutedRequest(AssertionError):
    """A request matched no route, so a test is missing a canned response."""


def _parse_key(key: str) -> tuple[str, dict[str, str]]:
    """Split ``"substring|k=v|k2=v2"`` into the substring and its parameters."""
    substring, *segments = key.split("|")
    params: dict[str, str] = {}
    for segment in segments:
        name, sep, value = segment.partition("=")
        if not sep:
            raise ValueError(f"route parameter {segment!r} in {key!r} is not k=v")
        params[name] = value
    return substring, params


class RoutedHttpClient:
    """An ``HttpClient`` that serves each request from a table of routes.

    Attributes:
        calls: Every requested URL with its query string, in request order.
    """

    def __init__(self, routes: Mapping[str, bytes | Exception]) -> None:
        """Index the routes; values are response bodies or exceptions to raise."""
        self._routes = [(*_parse_key(key), value) for key, value in routes.items()]
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpResponse:
        """Return the routed body as a 200 response, or raise the routed exception."""
        self.calls.append(f"{url}?{urlencode(sorted(params.items()))}")
        matches = [
            (len(wanted), len(substring), index, value)
            for index, (substring, wanted, value) in enumerate(self._routes)
            if substring in url and all(params.get(k) == v for k, v in wanted.items())
        ]
        if not matches:
            raise UnroutedRequest(f"no route for {url} with params {dict(params)}")
        # Most parameters, then longest substring; ties go to the earlier route.
        _, _, _, value = max(matches, key=lambda m: (m[0], m[1], -m[2]))
        if isinstance(value, Exception):
            raise value
        return HttpResponse(200, value, {})


__all__ = ["RoutedHttpClient", "UnroutedRequest"]
