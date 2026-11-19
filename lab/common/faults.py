"""Fault flags: the switches the fault injector flips to break a lab service on purpose.

Each service declares its own flags as a pydantic model (unknown flags are rejected) and gets
three internal routes:

    GET    /_faults   current flags
    PUT    /_faults   merge new flag values (validated)
    DELETE /_faults   reset every flag to its healthy default

Routes under ``/_`` are the lab's *control plane*. They are deliberately silent: the request
middleware neither logs nor counts them (see ``lab/common/http.py``). Otherwise the triage
agent could "solve" an incident by reading a log line that says which fault was injected,
which would make every eval score meaningless.

The injector, not the services, is the only thing that knows the true cause of a fault.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

OnChange = Callable[[BaseModel, BaseModel], Awaitable[None]]


class FaultStore[T: BaseModel]:
    """Holds the current flags of one service. Read with ``store.current.<flag>``."""

    def __init__(self, model: type[T]) -> None:
        self.model = model
        self.current: T = model()

    def merge(self, updates: dict[str, Any]) -> T:
        merged = {**self.current.model_dump(), **updates}
        return self.model.model_validate(merged)


def install_fault_routes(
    app: FastAPI, store: FaultStore, on_change: OnChange | None = None
) -> None:
    async def apply(new: BaseModel) -> BaseModel:
        old = store.current
        store.current = new
        if on_change is not None:
            await on_change(old, new)
        return new

    @app.get("/_faults", include_in_schema=False)
    async def get_faults() -> dict:
        return store.current.model_dump()

    @app.put("/_faults", include_in_schema=False)
    async def put_faults(updates: dict[str, Any]) -> dict:
        try:
            new = store.merge(updates)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False)) from None
        return (await apply(new)).model_dump()

    @app.delete("/_faults", include_in_schema=False)
    async def clear_faults() -> dict:
        return (await apply(store.model())).model_dump()
