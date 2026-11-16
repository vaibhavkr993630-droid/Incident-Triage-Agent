"""LokiHandler tests against a real local HTTP server that records what it receives."""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lab.common.log_setup import JsonFormatter
from lab.common.loki import LOG_LINES_DROPPED, LokiHandler, build_push_payload


def test_payload_groups_by_level_and_sorts_by_time():
    payload = build_push_payload(
        "shop-api",
        [(3, "error", "c"), (1, "info", "a"), (2, "info", "b")],
    )
    streams = {s["stream"]["level"]: s for s in payload["streams"]}
    assert streams["info"]["stream"] == {"service": "shop-api", "level": "info"}
    assert streams["info"]["values"] == [["1", "a"], ["2", "b"]]
    assert streams["error"]["values"] == [["3", "c"]]


class Recorder(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802 - name required by BaseHTTPRequestHandler
        length = int(self.headers["Content-Length"])
        Recorder.received.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_loki():
    Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def make_logger(handler: logging.Handler, name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def test_lines_are_pushed_as_json_with_service_and_level_labels(fake_loki):
    handler = LokiHandler(fake_loki, "worker", flush_interval=0.1)
    handler.setFormatter(JsonFormatter("worker"))
    log = make_logger(handler, "test.push")
    log.info("order fulfilled", extra={"fields": {"order_id": 5}})
    log.error("cannot read fulfillment queue")
    handler.close()

    assert Recorder.received, "nothing was pushed"
    assert all(r["path"] == "/loki/api/v1/push" for r in Recorder.received)
    lines = {}
    for request in Recorder.received:
        for stream in request["body"]["streams"]:
            assert stream["stream"]["service"] == "worker"
            for _ts, line in stream["values"]:
                lines[json.loads(line)["msg"]] = stream["stream"]["level"]
    assert lines == {"order fulfilled": "info", "cannot read fulfillment queue": "error"}


def test_unreachable_loki_drops_lines_without_blocking_the_caller():
    before = LOG_LINES_DROPPED.labels("push_failed")._value.get()
    # Port 9 (discard) on localhost is not listening: connection refused.
    handler = LokiHandler("http://127.0.0.1:9", "worker", flush_interval=0.05, timeout=0.5)
    handler.setFormatter(JsonFormatter("worker"))
    log = make_logger(handler, "test.down")

    start = time.perf_counter()
    for i in range(50):
        log.info("line %d", i)
    assert time.perf_counter() - start < 0.1, "logging blocked the caller"

    handler.close()
    after = LOG_LINES_DROPPED.labels("push_failed")._value.get()
    assert after - before == 50


def test_full_queue_drops_and_counts_instead_of_blocking():
    before = LOG_LINES_DROPPED.labels("queue_full")._value.get()
    handler = LokiHandler("http://127.0.0.1:9", "worker", max_queue=1, flush_interval=5)
    handler.setFormatter(JsonFormatter("worker"))
    log = make_logger(handler, "test.full")
    for i in range(20):
        log.info("line %d", i)
    after = LOG_LINES_DROPPED.labels("queue_full")._value.get()
    assert after - before >= 15
    handler._stop.set()
