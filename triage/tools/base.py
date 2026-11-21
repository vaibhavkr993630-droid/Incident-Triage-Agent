"""The tool framework: typed arguments, size-capped results, ids, and a call budget.

How a tool call flows (the model never executes anything itself):

    model asks: {"name": "query_logs", "arguments": "{\"service\": \"shop-api\"}"}
        -> ToolSession.call()   assigns an id (t1, t2, ...) and checks the call budget
        -> ToolRegistry         finds the tool, validates the arguments with its pydantic model
        -> Tool.run()           our code queries Prometheus / Loki / the services (read-only)
        -> result               compact JSON text, capped in size, recorded as a ToolCall

Every outcome, including bad arguments, an unknown tool or an unreachable backend, comes back
as text the model can read and react to. Nothing here raises into the agent loop.

The ToolCall ids matter later: the final report must cite them as evidence, and the evals
check that each cited id exists and its result really contains what the report claims.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ValidationError

from triage.config import Endpoints

# About 1,500 tokens. Big enough for a useful summary, small enough that ten tool calls fit
# comfortably in a free-tier model's context.
MAX_RESULT_CHARS = 6000

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ToolError(Exception):
    """An expected failure (bad query, backend error). Its message goes to the model."""


@dataclass
class ToolContext:
    endpoints: Endpoints = field(default_factory=Endpoints)
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10))
    clock: Clock = utc_now
    # Eval isolation: log searches and metric ranges never reach back before this moment.
    # The fault injector sets it to when the lab was last confirmed healthy, so leftovers
    # from a previous scenario cannot leak into this one. None (normal use) = no limit.
    not_before: datetime | None = None

    def window_start(self, end: datetime, minutes: int) -> datetime:
        start = end - timedelta(minutes=minutes)
        if self.not_before is not None and self.not_before > start:
            return self.not_before
        return start

    def get_json(self, url: str, params: dict | None = None, what: str = "backend") -> Any:
        """GET and decode JSON. This is the only way tools reach the network: read-only."""
        try:
            resp = self.http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ToolError(f"could not reach {what}: {type(exc).__name__}") from None
        try:
            body = resp.json()
        except ValueError:
            raise ToolError(
                f"{what} returned HTTP {resp.status_code} with a non-JSON body"
            ) from None
        if resp.status_code >= 400:
            detail = body.get("error") if isinstance(body, dict) else None
            raise ToolError(f"{what} returned HTTP {resp.status_code}: {detail or body}")
        return body


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def run(self, args: BaseModel) -> dict:
        raise NotImplementedError

    def schema(self) -> dict:
        """The tool as an OpenAI-compatible function definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters_schema(self.Args),
            },
        }


def parameters_schema(model: type[BaseModel]) -> dict:
    """A pydantic model as a function-calling parameter schema.

    Drops pydantic's "title" keys (noise for the model) and inlines "$ref" definitions,
    which several providers' function-calling schemas do not support.
    """
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].split("/")[-1]]
                rest = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**target, **rest})
            return {k: walk(v) for k, v in node.items() if k not in ("title", "$defs")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    result: str
    is_error: bool
    duration_ms: int
    truncated: bool = False


def to_text(payload: dict) -> tuple[str, bool]:
    """Serialize compactly; hard-cap the size as a last resort (tools trim themselves first)."""
    text = json.dumps(payload, separators=(",", ":"), default=str, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return text, False
    marker = f"...[truncated: result was {len(text)} chars, limit {MAX_RESULT_CHARS}]"
    return text[: MAX_RESULT_CHARS - len(marker)] + marker, True


def fit_list(payload: dict, key: str, budget: int = MAX_RESULT_CHARS) -> dict:
    """Drop items from the end of payload[key] until the JSON fits, and say how many.

    Lists are ordered most-important-first by each tool, so the tail is what is lost.
    """
    items = list(payload[key])
    omitted = 0
    while True:
        candidate = {**payload, key: items, f"omitted_{key}": omitted}
        if len(to_text(candidate)[0]) <= budget or not items:
            if omitted == 0:
                candidate.pop(f"omitted_{key}")
            return candidate
        items.pop()
        omitted += 1


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = {t.name: t for t in tools}

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def execute(self, call_id: str, name: str, raw_args: dict | str | None) -> ToolCall:
        start = time.perf_counter()

        def done(payload: dict, is_error: bool, args: dict) -> ToolCall:
            text, truncated = to_text(payload)
            elapsed = round((time.perf_counter() - start) * 1000)
            return ToolCall(call_id, name, args, text, is_error, elapsed, truncated)

        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                return done({"error": f"arguments are not valid JSON: {exc.msg}"}, True, {})
        raw_args = raw_args or {}
        if not isinstance(raw_args, dict):
            return done({"error": "arguments must be a JSON object"}, True, {})

        tool = self.tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self.tools))
            return done(
                {"error": f"unknown tool '{name}'. Available tools: {known}"}, True, raw_args
            )
        try:
            args = tool.Args.model_validate(raw_args)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
            )
            return done({"error": f"invalid arguments for {name}: {problems}"}, True, raw_args)
        try:
            return done(tool.run(args), False, args.model_dump(exclude_none=True))
        except ToolError as exc:
            return done({"error": str(exc)}, True, args.model_dump(exclude_none=True))
        except Exception as exc:  # noqa: BLE001 - never crash the agent loop
            return done({"error": f"internal tool error: {type(exc).__name__}"}, True, raw_args)


class ToolSession:
    """One investigation's tool calls: ids in order, a budget, and the full record (trace)."""

    def __init__(self, registry: ToolRegistry, max_calls: int = 20) -> None:
        self.registry = registry
        self.max_calls = max_calls
        self.calls: list[ToolCall] = []

    def call(self, name: str, raw_args: dict | str | None = None) -> ToolCall:
        call_id = f"t{len(self.calls) + 1}"
        if len(self.calls) >= self.max_calls:
            payload = {
                "error": f"tool call budget of {self.max_calls} is used up; write the report now"
            }
            record = ToolCall(call_id, name, {}, to_text(payload)[0], True, 0)
        else:
            record = self.registry.execute(call_id, name, raw_args)
        self.calls.append(record)
        return record

    def get(self, call_id: str) -> ToolCall | None:
        return next((c for c in self.calls if c.id == call_id), None)
