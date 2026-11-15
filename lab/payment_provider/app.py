"""Mock payment provider: an external dependency of the shop API.

It approves every charge after a short, realistic delay. Phase 3 adds fault flags here
(slow responses, error responses) to simulate a third-party outage.
"""

import asyncio
import logging
import random
import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field

from lab.common.http import install_http_middleware
from lab.common.log_setup import setup_logging
from lab.common.settings import load_settings

SERVICE = "payment-provider"

settings = load_settings()
setup_logging(SERVICE, settings.log_level)
log = logging.getLogger("payment_provider")

app = FastAPI(title="Lab payment provider")
install_http_middleware(app)


class ChargeIn(BaseModel):
    amount_cents: int = Field(gt=0)
    reference: str


class RefundIn(BaseModel):
    charge_id: str


@app.post("/charge")
async def charge(body: ChargeIn):
    await asyncio.sleep(random.uniform(0.02, 0.08))
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
