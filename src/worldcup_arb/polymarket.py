from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"


class PolymarketFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolymarketClient:
    gamma_host: str = GAMMA_HOST
    clob_host: str = CLOB_HOST
    timeout: float = 20.0

    def get_event_by_slug(self, slug: str) -> dict[str, Any]:
        return self._get_json(f"{self.gamma_host}/events/slug/{slug}")

    def get_order_book(self, token_id: str) -> dict[str, Any]:
        query = urlencode({"token_id": token_id})
        return self._get_json(f"{self.clob_host}/book?{query}")

    def get_geoblock(self) -> dict[str, Any]:
        return self._get_json("https://polymarket.com/api/geoblock")

    def snapshot_event(self, slug: str, *, include_books: bool = True) -> dict[str, Any]:
        event = self.get_event_by_slug(slug)
        snapshot: dict[str, Any] = {
            "slug": slug,
            "title": event.get("title"),
            "active": event.get("active"),
            "closed": event.get("closed"),
            "restricted": event.get("restricted"),
            "markets": [],
        }
        for market in event.get("markets", []):
            market_snapshot = {
                "id": market.get("id"),
                "question": market.get("question"),
                "conditionId": market.get("conditionId"),
                "outcomes": _json_list(market.get("outcomes")),
                "clobTokenIds": _json_list(market.get("clobTokenIds")),
                "outcomePrices": _json_list(market.get("outcomePrices")),
                "bestBid": market.get("bestBid"),
                "bestAsk": market.get("bestAsk"),
                "lastTradePrice": market.get("lastTradePrice"),
                "feesEnabled": market.get("feesEnabled"),
                "feeSchedule": market.get("feeSchedule"),
                "books": {},
            }
            if include_books:
                for token_id in market_snapshot["clobTokenIds"]:
                    try:
                        market_snapshot["books"][token_id] = self.get_order_book(str(token_id))
                    except PolymarketFetchError as exc:
                        market_snapshot["books"][token_id] = {"error": str(exc)}
            snapshot["markets"].append(market_snapshot)
        return snapshot

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "worldcup-arb/0.1"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read()
        except HTTPError as exc:
            raise PolymarketFetchError(f"HTTP {exc.code} for {url}") from exc
        except URLError as exc:
            raise PolymarketFetchError(f"network error for {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise PolymarketFetchError(f"timeout for {url}") from exc

        try:
            decoded = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PolymarketFetchError(f"invalid JSON from {url}") from exc
        if not isinstance(decoded, dict):
            raise PolymarketFetchError(f"unexpected JSON payload from {url}")
        return decoded


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return decoded if isinstance(decoded, list) else [decoded]
    return [value]
