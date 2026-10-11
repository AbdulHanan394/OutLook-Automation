import threading
import time
from collections import defaultdict, deque
from uuid import UUID

from fastapi import HTTPException

_requests: dict[tuple[UUID, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def enforce_user_rate_limit(user_id: UUID, action: str, limit: int, window_seconds: int = 60) -> None:
    now = time.monotonic()
    with _lock:
        requests = _requests[(user_id, action)]
        while requests and now - requests[0] >= window_seconds:
            requests.popleft()
        if len(requests) >= limit:
            raise HTTPException(429, "Too many requests. Please wait a moment.")
        requests.append(now)
