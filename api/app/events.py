"""Audit log + in-memory fan-out to Server-Sent-Event subscribers."""
import asyncio
import json
from typing import Any

from .db import Event, SessionLocal


class Bus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def emit(self, type_: str, payload: dict[str, Any], run_id: int | None = None) -> dict:
        with SessionLocal() as s:
            ev = Event(run_id=run_id, type=type_, payload=payload)
            s.add(ev)
            s.commit()
            data = {"id": ev.id, "run_id": run_id, "type": type_, "payload": payload,
                    "ts": ev.created_at.isoformat()}
        for q in list(self._subs):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass
        return data


bus = Bus()


def sse_format(data: dict) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"
