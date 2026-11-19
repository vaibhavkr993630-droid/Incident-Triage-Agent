import json
import logging

from lab.common.log_setup import JsonFormatter


def make_record(
    msg: str, fields: dict | None = None, level: int = logging.INFO
) -> logging.LogRecord:
    record = logging.LogRecord("shop_api", level, __file__, 1, msg, None, None)
    if fields is not None:
        record.fields = fields
    return record


def test_log_line_is_one_json_object_with_contract_fields():
    line = JsonFormatter("shop-api").format(make_record("order created", {"order_id": 7}))
    entry = json.loads(line)
    assert entry["service"] == "shop-api"
    assert entry["level"] == "info"
    assert entry["msg"] == "order created"
    assert entry["order_id"] == 7
    assert entry["ts"].endswith("+00:00")


def test_structured_fields_cannot_overwrite_contract_fields():
    line = JsonFormatter("shop-api").format(
        make_record("real message", {"msg": "spoofed", "service": "x"})
    )
    entry = json.loads(line)
    assert entry["msg"] == "real message"
    assert entry["service"] == "shop-api"


def test_exceptions_are_included():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record("job failed", level=logging.ERROR)
        record.exc_info = sys.exc_info()
    entry = json.loads(JsonFormatter("worker").format(record))
    assert entry["level"] == "error"
    assert "ValueError: boom" in entry["exception"]


def test_uvicorn_tracebacks_go_through_the_json_handler(monkeypatch, capsys):
    """Unhandled-exception tracebacks are logged by uvicorn's logger; they must be JSON too."""
    import logging as stdlib_logging

    from lab.common.log_setup import setup_logging

    monkeypatch.delenv("LOKI_URL", raising=False)
    # Simulate uvicorn's default config: its own plain-text handler, no propagation.
    uv_error = stdlib_logging.getLogger("uvicorn.error")
    uv_error.addHandler(stdlib_logging.StreamHandler())
    stdlib_logging.getLogger("uvicorn").propagate = False
    root = stdlib_logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        setup_logging("shop-api")
        try:
            raise KeyError("discount_cents")
        except KeyError:
            uv_error.exception("Exception in ASGI application")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        entry = json.loads(line)
        assert entry["msg"] == "Exception in ASGI application"
        assert "KeyError: 'discount_cents'" in entry["exception"]
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)
