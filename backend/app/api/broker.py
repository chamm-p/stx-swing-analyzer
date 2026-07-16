"""Broker-API (IBKR): Status/Verbindungstest und Order-Platzierung.

Orders erfordern doppeltes Opt-in: ``trading_enabled`` in den Settings
UND ``confirm: true`` im Request — kein Endpoint feuert versehentlich.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_user
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class IbkrSettings(BaseModel):
    account: str | None = None
    consumer_key: str | None = None
    access_token: str | None = None
    access_token_secret: str | None = None  # leer = Bestand behalten
    trading_enabled: str | None = None  # "true"/"false"


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    side: str = Field(pattern="^(BUY|SELL)$")
    quantity: float = Field(gt=0)
    order_type: str = Field(default="MKT", pattern="^(MKT|LMT)$")
    limit_price: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    currency: str | None = None
    confirm: bool = False


@router.get("/broker/ibkr/status")
async def ibkr_status(db: AsyncSession = Depends(get_db)):
    """Verbindungstest: Konto-Kennzahlen + offene IBKR-Positionen."""
    from app.broker import ibkr

    try:
        return await ibkr.status(db)
    except Exception as e:
        logger.warning("IBKR-Status fehlgeschlagen: %s", e)
        raise HTTPException(status_code=502, detail=(
            f"IBKR-Web-API nicht erreichbar: {e} — OAuth-Zugangsdaten in den "
            "Einstellungen und Schlüsseldateien unter secrets/ibkr prüfen. "
            "Hinweis: Neue Consumer-Keys aktiviert IBKR erst beim "
            "Wochenend-Neustart."))


@router.put("/settings/ibkr")
async def put_ibkr(payload: IbkrSettings, db: AsyncSession = Depends(get_db)):
    from app.services_settings import public_view, save_settings

    data = payload.model_dump(exclude_none=True)
    if "trading_enabled" in data and data["trading_enabled"].strip().lower() not in (
            "", "true", "false"):
        raise HTTPException(status_code=422, detail="trading_enabled muss true/false sein")
    await save_settings(db, "ibkr", data)
    return await public_view(db, "ibkr")


@router.get("/settings/ibkr")
async def get_ibkr(db: AsyncSession = Depends(get_db)):
    """OAuth-Status inkl. Schlüsseldatei-Check (Secret bleibt write-only)."""
    from app.broker.ibkr import _missing_config
    from app.services_settings import load_settings, public_view

    view = await public_view(db, "ibkr")
    cfg = await load_settings(db, "ibkr")
    view["missing"] = _missing_config(cfg)
    return view


@router.post("/broker/ibkr/order")
async def ibkr_order(payload: OrderRequest, db: AsyncSession = Depends(get_db)):
    """Order an IBKR — nur mit Settings-Opt-in UND confirm-Flag."""
    from app.broker import ibkr

    if not payload.confirm:
        raise HTTPException(status_code=422,
                            detail="Order nicht bestätigt (confirm fehlt)")
    try:
        result = await ibkr.place_order(
            db, symbol=payload.symbol, side=payload.side,
            quantity=payload.quantity, order_type=payload.order_type,
            limit_price=payload.limit_price, take_profit=payload.take_profit,
            stop_loss=payload.stop_loss, currency=payload.currency,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("IBKR-Order fehlgeschlagen: %s", e)
        raise HTTPException(status_code=502, detail=f"IBKR-Order fehlgeschlagen: {e}")
    logger.info("IBKR-Order %s %s×%s → %s", payload.side, payload.quantity,
                payload.symbol, result["status"])
    return result
