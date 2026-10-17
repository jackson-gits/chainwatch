"""WebSocket hub (spec 13.3): push-only, one bounded queue (100 batches) per client, oldest dropped
when full."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque

QUEUE_BATCHES = 100


class _Client:
    def __init__(self, ws):
        self.ws = ws
        self.queue: deque = deque()
        self.dropped = 0
        self.event = asyncio.Event()


class WsHub:
    def __init__(self, batch_ms: int = 250, snapshot_fn=None, extras_fn=None):
        self.batch_s = batch_ms / 1000.0
        self.clients: set[_Client] = set()
        self.pending_items: list = []
        self.pending_ring_events: list = []
        self.recent: deque = deque(maxlen=200)
        self.snapshot_fn = snapshot_fn  # () -> dict for the "snapshot" message
        self.extras_fn = extras_fn  # () -> dict merged into each batch (replay status, metrics)
        self._tasks: list[asyncio.Task] = []

    # -------------------------------------------------------------- publishing (called from the event loop)
    def publish(self, items: list, ring_events: list) -> None:
        if items:
            self.pending_items.extend(items)
            self.recent.extend(items)
        if ring_events:
            self.pending_ring_events.extend(ring_events)

    def send_now(self, message: dict) -> None:
        self._enqueue(message)

    def _enqueue(self, message: dict) -> None:
        for c in list(self.clients):
            if len(c.queue) >= QUEUE_BATCHES:
                c.queue.popleft()
                c.dropped += 1
            c.queue.append(message)
            c.event.set()

    async def flusher(self) -> None:
        while True:
            await asyncio.sleep(self.batch_s)
            if not self.pending_items and not self.pending_ring_events:
                continue
            msg = {"type": "batch", "items": self.pending_items, "ring_events": self.pending_ring_events}
            self.pending_items, self.pending_ring_events = [], []
            if self.extras_fn is not None:
                msg.update(self.extras_fn())
            self._enqueue(msg)

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(15.0)
            self._enqueue({"type": "heartbeat", "ts": time.time()})

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self.flusher()), loop.create_task(self.heartbeat())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # -------------------------------------------------------------- per-connection
    async def serve(self, ws) -> None:
        """Run one connection: send the snapshot, then queued messages; read and ignore client input."""
        client = _Client(ws)
        snap = {"type": "snapshot", "recent": list(self.recent)}
        if self.snapshot_fn is not None:
            snap.update(self.snapshot_fn())
        await ws.send_text(json.dumps(snap))
        self.clients.add(client)

        async def reader():
            while True:
                await ws.receive_text()  # pings and anything else are ignored

        rtask = asyncio.create_task(reader())
        try:
            while True:
                if not client.queue:
                    client.event.clear()
                    waiter = asyncio.create_task(client.event.wait())
                    done, _ = await asyncio.wait({waiter, rtask}, return_when=asyncio.FIRST_COMPLETED)
                    if rtask in done:
                        waiter.cancel()
                        break
                    continue
                msg = client.queue.popleft()
                if client.dropped and msg.get("type") == "batch":
                    msg = dict(msg)
                    msg["dropped"] = client.dropped
                    client.dropped = 0
                elif msg.get("type") == "batch":
                    msg = dict(msg)
                    msg["dropped"] = 0
                await ws.send_text(json.dumps(msg))
        except Exception:
            pass
        finally:
            self.clients.discard(client)
            rtask.cancel()
