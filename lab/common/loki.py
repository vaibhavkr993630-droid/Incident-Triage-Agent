"""Ship log lines straight to Loki's push API.

Production setups usually run a separate agent (Grafana Alloy, Fluent Bit) that tails container
logs. The lab pushes from inside each service instead: one less container on a small laptop,
and it works the same in CI. The trade-off is documented in the README.

Design rules:
- ``emit`` never blocks and never raises. Logging must not slow down or break a request, so a
  full queue drops the line and counts it.
- A background thread sends batches every second (or sooner when a batch fills up).
- If Loki is down, the batch is dropped after one failed send. The lab is a test system;
  losing logs during a Loki outage is acceptable, blocking the app is not.
- Labels are only ``service`` and ``level`` (low cardinality). Everything else stays inside the
  JSON line and is filtered at query time with LogQL's ``| json``.
"""

import json
import logging
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

from prometheus_client import Counter

LOG_LINES_DROPPED = Counter(
    "log_shipping_dropped_total", "Log lines not delivered to Loki.", ["reason"]
)

Entry = tuple[int, str, str]  # (timestamp in ns, level, formatted line)


def build_push_payload(service: str, entries: list[Entry]) -> dict:
    """Group entries into one Loki stream per level, oldest first."""
    streams: dict[str, list[list[str]]] = defaultdict(list)
    for ts_ns, level, line in sorted(entries, key=lambda e: e[0]):
        streams[level].append([str(ts_ns), line])
    return {
        "streams": [
            {"stream": {"service": service, "level": level}, "values": values}
            for level, values in streams.items()
        ]
    }


class LokiHandler(logging.Handler):
    def __init__(
        self,
        url: str,
        service: str,
        *,
        batch_size: int = 200,
        flush_interval: float = 1.0,
        max_queue: int = 10_000,
        timeout: float = 2.0,
    ) -> None:
        super().__init__()
        self.push_url = url.rstrip("/") + "/loki/api/v1/push"
        self.service = service
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.timeout = timeout
        self._queue: queue.Queue[Entry] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="loki-shipper", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = (int(record.created * 1e9), record.levelname.lower(), self.format(record))
            self._queue.put_nowait(entry)
        except queue.Full:
            LOG_LINES_DROPPED.labels("queue_full").inc()
        except Exception:  # noqa: BLE001 - a log handler must never raise into the app
            self.handleError(record)

    def _drain(self) -> list[Entry]:
        batch: list[Entry] = []
        deadline = time.monotonic() + self.flush_interval
        while len(batch) < self.batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self._queue.get(timeout=remaining))
            except queue.Empty:
                break
        return batch

    def _send(self, batch: list[Entry]) -> None:
        body = json.dumps(build_push_payload(self.service, batch)).encode()
        request = urllib.request.Request(
            self.push_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except (urllib.error.URLError, OSError) as exc:
            LOG_LINES_DROPPED.labels("push_failed").inc(len(batch))
            # stderr, not logging: logging here would feed back into this handler.
            print(f"loki push failed, dropped {len(batch)} lines: {exc}", file=sys.stderr)

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._drain()
            if batch:
                self._send(batch)

    def close(self) -> None:
        """Send whatever is still queued, then stop the thread."""
        self._stop.set()
        self._thread.join(timeout=self.timeout + self.flush_interval + 1)
        leftover: list[Entry] = []
        while True:
            try:
                leftover.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if leftover:
            self._send(leftover)
        super().close()
