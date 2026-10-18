"""Measure a real chat turn against a real mailbox and a real provider.

Run against the demo mailbox, never the reader's own. Reports what the
specification asks for and does not guess any of it:

  * time to first token, measured from request start to the first answer_delta
  * tool steps per answer
  * provider calls and tokens per turn

Usage:
    python -m eval.measure_chat [--turns 3] [--base http://localhost:8000]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from uuid import uuid4

QUESTIONS = [
    "what did they say about the launch date?",
    "what should I follow up on this week?",
    "summarise the most recent conversations",
]


def _cookie(name: str, value: str, base: str):
    from http.cookiejar import Cookie

    host = urllib.parse.urlsplit(base).hostname or "localhost"
    return Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=host, domain_specified=False, domain_initial_dot=False,
        path="/", path_specified=True, secure=False, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )


class Session:
    """A cookie-jar client, so the session cookie and CSRF token travel."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def _csrf(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrf_token":
                return cookie.value or ""
        return ""

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        data = json.dumps(body).encode() if body is not None else None
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.jar)
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self._csrf(),
                "Cookie": cookie,
            },
        )
        try:
            with self.opener.open(req, timeout=180) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")


def stream_turn(session: Session, conversation_id: str, message: str) -> dict[str, Any]:
    """One turn, timed to the first token.

    ``urlopen`` hands back the whole body, so time-to-first-token cannot be
    measured through it -- the stream arrives when the response does. The
    socket is read incrementally instead, which is the only honest way to get
    this number.
    """
    import http.client
    import urllib.parse

    parts = urllib.parse.urlsplit(session.base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=180)
    body = json.dumps({"message": message})
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": session._csrf(),
        "Cookie": "; ".join(f"{c.name}={c.value}" for c in session.jar),
    }
    path = f"/api/chats/{conversation_id}/messages"
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=180)

    started = time.perf_counter()
    conn.request("POST", path, body=body, headers=headers)
    response = conn.getresponse()

    first_token: float | None = None
    buffer = b""
    name = ""
    chunks = 0
    while True:
        piece = response.read(1)
        if not piece:
            break
        buffer += piece
        if piece == b"\n" and buffer.endswith(b"\n\n"):
            block = buffer.decode("utf-8", "replace")
            for line in block.splitlines():
                if line.startswith("event: "):
                    name = line[7:].strip()
            if name == "answer_delta":
                chunks += 1
                if first_token is None:
                    first_token = time.perf_counter() - started
            buffer = b""
            name = ""
    total = time.perf_counter() - started
    conn.close()

    return {
        "time_to_first_token_s": round(first_token, 3) if first_token else None,
        "total_s": round(total, 3),
        "delta_chunks": chunks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--mailbox-index", type=int, default=0)
    args = parser.parse_args()

    session = Session(args.base)

    # Reuse the minted demo session rather than logging in again.
    demo = Path("eval/.demo_session.json")
    if demo.exists():
        payload = json.loads(demo.read_text(encoding="utf-8"))
        for name, value in (
            ("email_finder_session", payload["session_token"]),
            ("csrf_token", payload["csrf_token"]),
        ):
            session.jar.set_cookie(_cookie(name, value, args.base))

    status, body = session.request("GET", "/api/mailboxes")
    if status != 200:
        print(f"could not list mailboxes: {status} {body[:200]}", file=sys.stderr)
        return 1
    parsed = json.loads(body)
    mailboxes = parsed if isinstance(parsed, list) else parsed.get("mailboxes", [])
    if not mailboxes:
        print("no mailbox connected", file=sys.stderr)
        return 1
    mailbox = mailboxes[min(args.mailbox_index, len(mailboxes) - 1)]
    print(f"mailbox: {mailbox.get('email')}")

    results: list[dict[str, Any]] = []
    for index in range(args.turns):
        question = QUESTIONS[index % len(QUESTIONS)]
        status, body = session.request("POST", "/api/chats", {"mailbox_id": mailbox["id"]})
        if status != 201:
            print(f"could not create chat: {status} {body[:200]}", file=sys.stderr)
            return 1
        conversation_id = json.loads(body)["id"]

        timings = stream_turn(session, conversation_id, question)
        status, body = session.request("GET", f"/api/chats/{conversation_id}")
        if status != 200:
            print(f"could not read chat back: {status}", file=sys.stderr)
            continue
        messages = json.loads(body).get("messages", [])
        assistant = next((m for m in messages if m["role"] == "assistant"), None)
        steps = assistant.get("steps", []) if assistant else []
        usage = (assistant or {}).get("content", {}).get("usage", {})

        row = {
            "question": question,
            **timings,
            "tool_steps": len(steps),
            "tools": [s.get("tool") for s in steps],
            "answer_chars": len((assistant or {}).get("text", "")),
            "citations": len((assistant or {}).get("citations", [])),
            "calls": usage.get("calls"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False))

        session.request("DELETE", f"/api/chats/{conversation_id}")

    if results:
        firsts = [r["time_to_first_token_s"] for r in results if r["time_to_first_token_s"]]
        totals = [r["total_s"] for r in results]
        steps = [r["tool_steps"] for r in results]
        calls = [r["calls"] for r in results if r["calls"] is not None]
        tokens = [
            (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
            for r in results
            if r["input_tokens"] is not None
        ]
        print("\n--- summary ---")
        if firsts:
            print(f"  time to first token : median {statistics.median(firsts):.2f}s "
                  f"(min {min(firsts):.2f}, max {max(firsts):.2f})")
        print(f"  total turn time    : median {statistics.median(totals):.2f}s")
        print(f"  tool steps per turn: median {statistics.median(steps):.1f}")
        if calls:
            print(f"  provider calls/turn: median {statistics.median(calls):.1f}")
        if tokens:
            print(f"  tokens per turn    : median {statistics.median(tokens):.0f}")

    out = Path("eval/result-chat.json")
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())