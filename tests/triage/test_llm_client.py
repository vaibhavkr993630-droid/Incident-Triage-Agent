"""The OpenAI-compatible client against a real local HTTP server with scripted replies."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from triage.llm import LLMError, OpenAICompatibleClient, Provider, load_dotenv


def ok_body(tool_calls=None, content=""):
    return {
        "choices": [
            {"message": {"content": content, "tool_calls": tool_calls}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 7},
    }


class Server:
    """Pops one scripted (status, body, headers) per request; records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers["Content-Length"])
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(self.rfile.read(length)),
                    }  # fmt: skip
                )
                status, body, headers = outer.script.pop(0)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server(request):
    s = Server(request.param)
    yield s
    s.close()


def provider(server, name="primary", **kw) -> Provider:
    return Provider(name=name, base_url=server.url, model="m-1", api_key="secret", **kw)


CALL = [{"id": "x1", "type": "function",
         "function": {"name": "query_logs", "arguments": '{"level":"error"}'}}]  # fmt: skip


@pytest.mark.parametrize("server", [[(200, ok_body(CALL), {})]], indirect=True)
def test_request_shape_and_parsed_reply(server):
    client = OpenAICompatibleClient(provider(server, reasoning_effort="low"))
    tools = [{"type": "function", "function": {"name": "query_logs", "parameters": {}}}]
    result = client.chat([{"role": "user", "content": "hi"}], tools)
    sent = server.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer secret"
    assert sent["body"]["model"] == "m-1" and sent["body"]["tool_choice"] == "auto"
    assert sent["body"]["reasoning_effort"] == "low" and sent["body"]["tools"] == tools
    assert result.tool_calls[0].name == "query_logs"
    assert result.tool_calls[0].arguments == '{"level":"error"}'
    assert result.usage.total == 57 and result.attempts == 1
    assert result.as_message()["tool_calls"][0]["id"] == "x1"


@pytest.mark.parametrize(
    "server", [[(429, {"error": "slow down"}, {"Retry-After": "1"}), (200, ok_body(), {})]],
    indirect=True,
)  # fmt: skip
def test_rate_limit_is_retried_after_the_advertised_wait(server):
    waits = []
    client = OpenAICompatibleClient(provider(server), sleep=waits.append)
    result = client.chat([], [])
    assert result.attempts == 2 and waits == [1.0]


@pytest.mark.parametrize("server", [[(401, {"error": "bad key"}, {})]], indirect=True)
def test_configuration_errors_fail_at_once_without_fallback(server):
    fallback = Provider(name="fallback", base_url="http://127.0.0.1:9/v1", model="f")
    client = OpenAICompatibleClient(provider(server), fallback, sleep=lambda s: None)
    with pytest.raises(LLMError) as exc:
        client.chat([], [])
    assert exc.value.status == 401 and "bad key" in str(exc.value)
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "server",
    [[(503, {"error": "overloaded"}, {})] * 3 + [(200, ok_body(content="from fallback"), {})]],
    indirect=True,
)
def test_an_outage_falls_back_to_the_second_provider(server):
    client = OpenAICompatibleClient(
        provider(server), provider(server, name="fallback"), sleep=lambda s: None
    )
    result = client.chat([], [])
    assert result.provider == "fallback" and result.content == "from fallback"
    assert len(server.requests) == 4


@pytest.mark.parametrize(
    "server",
    [
        [
            (
                200,
                ok_body([{"type": "function", "function": {"name": "t", "arguments": {"a": 1}}}]),
                {},
            )
        ]
    ],
    indirect=True,
)
def test_missing_ids_and_object_arguments_are_normalized(server):
    call = OpenAICompatibleClient(provider(server)).chat([], []).tool_calls[0]
    assert call.id.startswith("call_") and call.arguments == '{"a": 1}'


def test_dotenv_loading(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nTRIAGE_TEST_A="one"\nTRIAGE_TEST_B=two\nnot a line\n')
    monkeypatch.setenv("TRIAGE_TEST_B", "already set")
    monkeypatch.delenv("TRIAGE_TEST_A", raising=False)
    load_dotenv(env)
    import os

    assert os.environ["TRIAGE_TEST_A"] == "one"
    assert os.environ["TRIAGE_TEST_B"] == "already set"  # the real environment wins


GOOGLE_QUOTA = [{"error": {"code": 429, "message": "You exceeded your current quota",
                           "details": [{"retryDelay": "61157s"}]}}]  # fmt: skip


@pytest.mark.parametrize(
    "server", [[(429, GOOGLE_QUOTA, {}), (200, ok_body(content="fallback answered"), {})]],
    indirect=True,
)  # fmt: skip
def test_a_used_up_quota_skips_retries_and_goes_straight_to_the_fallback(server):
    """Google sends the wait in the body; 17 hours is not worth waiting for."""
    waits = []
    client = OpenAICompatibleClient(
        provider(server), provider(server, name="fallback"), sleep=waits.append
    )
    result = client.chat([], [])
    assert result.content == "fallback answered" and result.provider == "fallback"
    assert waits == [] and len(server.requests) == 2


def test_fallback_defaults(monkeypatch):
    from triage.llm import providers_from_env

    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-TEST-KEY")
    for name in ("GROQ_API_KEY", "TRIAGE_PROVIDER", "TRIAGE_MODEL", "TRIAGE_FALLBACK_PROVIDER",
                 "TRIAGE_FALLBACK_MODEL"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    primary, fallback = providers_from_env()
    assert (primary.name, primary.model) == ("gemini", "gemini-3.5-flash-lite")
    assert (fallback.name, fallback.model) == ("gemini", "gemini-3.1-flash-lite")
    assert "SECRET-TEST-KEY" not in repr(primary)  # the key never appears in repr or logs
    monkeypatch.setenv("GROQ_API_KEY", "q")
    assert providers_from_env()[1].name == "groq"


SIGNED = [
    {
        "id": "c1",
        "type": "function",
        "function": {"name": "service_health", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "sig-abc"}},
    }
]


@pytest.mark.parametrize("server", [[(200, ok_body(SIGNED), {})]], indirect=True)
def test_provider_specific_fields_are_sent_back_unchanged(server):
    """Gemini 3 rejects the next request (HTTP 400) if the thought signature is dropped."""
    result = OpenAICompatibleClient(provider(server)).chat([], [])
    echoed = result.as_message()["tool_calls"][0]
    assert echoed["extra_content"] == {"google": {"thought_signature": "sig-abc"}}
    assert echoed["id"] == "c1" and echoed["function"]["name"] == "service_health"
