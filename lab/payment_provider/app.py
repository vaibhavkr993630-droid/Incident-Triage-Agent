"""Mock payment provider: an external dependency of the shop API.

Healthy, it approves every charge after a short, realistic delay. Its fault flags simulate a
third-party outage: extra latency, or a share of requests failing with HTTP 500.
"""

import asyncio
import logging
import random
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lab.common.faults import FaultStore, install_fault_routes
from lab.common.http import install_http_middleware
from lab.common.log_setup import setup_logging
from lab.common.settings import load_settings

SERVICE = "payment-provider"

settings = load_settings()
setup_logging(SERVICE, settings.log_level)
log = logging.getLogger("payment_provider")


class ProviderFaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latency_ms: int = Field(default=0, ge=0, le=30_000)
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)


faults = FaultStore(ProviderFaults)

app = FastAPI(title="Lab payment provider")
install_http_middleware(app)
install_fault_routes(app, faults)


class ChargeIn(BaseModel):
    amount_cents: int = Field(gt=0)
    reference: str


class RefundIn(BaseModel):
    charge_id: str


@app.post("/charge")
async def charge(body: ChargeIn):
    flags = faults.current
    await asyncio.sleep(random.uniform(0.02, 0.08) + flags.latency_ms / 1000)
    if flags.error_rate and random.random() < flags.error_rate:
        log.error(
            "charge failed: card processor unavailable",
            extra={"fields": {"amount_cents": body.amount_cents, "reference": body.reference}},
        )
        raise HTTPException(500, "card processor unavailable")
    charge_id = f"ch_{uuid.uuid4().hex[:12]}"
    log.info(
        "charge approved",
        extra={"fields": {"charge_id": charge_id, "amount_cents": body.amount_cents}},
    )
    return {"charge_id": charge_id, "status": "approved"}


@app.post("/refund")
async def refund(body: RefundIn):
    log.info("refund issued", extra={"fields": {"charge_id": body.charge_id}})
    return {"charge_id": body.charge_id, "status": "refunded"}


@app.get("/health")
async def health():
    return {"status": "ok"}
