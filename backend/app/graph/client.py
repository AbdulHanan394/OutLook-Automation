import asyncio
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx


class GraphError(RuntimeError):
    pass


class GraphPayloadTooLarge(GraphError):
    pass


class GraphClient:
    def __init__(self, token: str, base_url: str = "https://graph.microsoft.com/v1.0") -> None:
        self.token, self.base_url = token, base_url.rstrip("/")

    @staticmethod
    def _retry_delay(value: str | None, attempt: int) -> float:
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(min(30, 2**attempt + random.random()))

    async def get(self, url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        target = url if url.startswith("http") else f"{self.base_url}{url}"
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(5):
                response = await client.get(target, params=params, headers={"Authorization": f"Bearer {self.token}", "Prefer": 'IdType="ImmutableId"'})
                if response.status_code not in (429, 500, 502, 503, 504):
                    response.raise_for_status()
                    return cast(dict[str, Any], response.json())
                if attempt == 4:
                    raise GraphError(
                        f"Graph request failed with status {response.status_code}"
                    )
                await asyncio.sleep(self._retry_delay(response.headers.get("Retry-After"), attempt))
        raise GraphError("unreachable")

    async def get_bytes(self, url: str, *, max_bytes: int) -> bytes:
        target = url if url.startswith("http") else f"{self.base_url}{url}"
        headers = {"Authorization": f"Bearer {self.token}", "Prefer": 'IdType="ImmutableId"'}
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(5):
                async with client.stream("GET", target, headers=headers) as response:
                    if response.status_code in (429, 500, 502, 503, 504):
                        if attempt == 4:
                            raise GraphError(f"Graph request failed with status {response.status_code}")
                        delay = self._retry_delay(response.headers.get("Retry-After"), attempt)
                    else:
                        response.raise_for_status()
                        length = response.headers.get("Content-Length")
                        if length:
                            try:
                                if int(length) > max_bytes:
                                    raise GraphPayloadTooLarge(
                                        "Graph attachment exceeds configured size limit"
                                    )
                            except ValueError:
                                raise GraphError(
                                    "Graph returned an invalid attachment size"
                                ) from None
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > max_bytes:
                                raise GraphPayloadTooLarge(
                                    "Graph attachment exceeds configured size limit"
                                )
                            body.extend(chunk)
                        return bytes(body)
                await asyncio.sleep(delay)
        raise GraphError("unreachable")
