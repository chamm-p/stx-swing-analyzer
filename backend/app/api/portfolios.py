"""Portfolio-API: echte und Trial-Portfolios, Positionen, Equity-Kurve."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.portfolio_calc import equity_curve, position_value
from app.auth.deps import require_user
from app.database import get_db
from app.models import Portfolio, Position, utcnow
from app.sources import yahoo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class AutoConfig(BaseModel):
    start_capital: float = Field(default=10000.0, gt=0)
    max_per_trade: float = Field(default=1000.0, gt=0)
    max_positions: int = Field(default=10, ge=1, le=50)
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    use_screener: bool = True
    enabled: bool = True


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="real", pattern="^(real|trial|auto)$")
    config: AutoConfig | None = None


class PositionCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    quantity: float = Field(gt=0)
    entry_price: float | None = Field(default=None, gt=0)
    notes: str | None = None


class PositionClose(BaseModel):
    exit_price: float | None = Field(default=None, gt=0)


class PortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    watch_enabled: bool | None = None
    config: AutoConfig | None = None


def _position_dict(p: Position, current: float | None) -> dict:
    return {
        "id": str(p.id), "symbol": p.symbol, "quantity": p.quantity,
        "entry_price": p.entry_price, "entry_date": p.entry_date,
        "exit_price": p.exit_price, "exit_date": p.exit_date,
        "notes": p.notes, "is_open": p.exit_date is None,
        "source": p.source, "horizon_days": p.horizon_days,
        **position_value(p, current),
    }


async def _portfolio_summary(db: AsyncSession, portfolio: Portfolio) -> dict:
    result = await db.execute(select(Position).where(Position.portfolio_id == portfolio.id))
    positions = result.scalars().all()
    value = invested = realized = 0.0
    open_count = 0
    for p in positions:
        current = await yahoo.latest_close(db, p.symbol) if p.exit_date is None else None
        pv = position_value(p, current)
        if p.exit_date is None:
            open_count += 1
            invested += pv["invested"]
            if pv["value"] is not None:
                value += pv["value"]
        elif pv["pnl_abs"] is not None:
            realized += pv["pnl_abs"]
    out = {
        "id": portfolio.id, "name": portfolio.name, "kind": portfolio.kind,
        "watch_enabled": portfolio.watch_enabled,
        "created_at": portfolio.created_at,
        "open_positions": open_count,
        "invested": round(invested, 2),
        "value": round(value, 2),
        "pnl_abs": round(value - invested, 2),
        "pnl_pct": round((value - invested) / invested * 100, 2) if invested else 0.0,
        "realized_pnl": round(realized, 2),
    }
    if portfolio.kind == "auto":
        cfg = portfolio.config or {}
        start = cfg.get("start_capital") or 0.0
        total = value + portfolio.cash
        out.update({
            "cash": round(portfolio.cash, 2),
            "config": cfg,
            "total_value": round(total, 2),
            "total_pnl_abs": round(total - start, 2),
            "total_pnl_pct": round((total - start) / start * 100, 2) if start else 0.0,
        })
    return out


@router.get("/portfolios")
async def list_portfolios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).order_by(Portfolio.created_at))
    return [await _portfolio_summary(db, p) for p in result.scalars().all()]


@router.post("/portfolios", status_code=201)
async def create_portfolio(payload: PortfolioCreate, db: AsyncSession = Depends(get_db)):
    portfolio = Portfolio(name=payload.name.strip(), kind=payload.kind)
    if payload.kind == "auto":
        cfg = (payload.config or AutoConfig()).model_dump()
        portfolio.config = cfg
        portfolio.cash = cfg["start_capital"]
    db.add(portfolio)
    await db.commit()
    return {"id": portfolio.id, "ok": True}


@router.patch("/portfolios/{portfolio_id}")
async def update_portfolio(portfolio_id: int, payload: PortfolioUpdate,
                           db: AsyncSession = Depends(get_db)):
    portfolio = await db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio nicht gefunden")
    if payload.name is not None:
        portfolio.name = payload.name.strip()
    if payload.watch_enabled is not None:
        portfolio.watch_enabled = payload.watch_enabled
    if payload.config is not None and portfolio.kind == "auto":
        portfolio.config = payload.config.model_dump()
    await db.commit()
    return {"ok": True}


@router.delete("/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: int, db: AsyncSession = Depends(get_db)):
    portfolio = await db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio nicht gefunden")
    await db.delete(portfolio)  # Positionen via FK-Cascade
    await db.commit()
    return {"ok": True}


@router.get("/portfolios/{portfolio_id}")
async def portfolio_detail(portfolio_id: int, db: AsyncSession = Depends(get_db)):
    portfolio = await db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio nicht gefunden")
    result = await db.execute(
        select(Position).where(Position.portfolio_id == portfolio_id)
        .order_by(Position.exit_date.isnot(None), Position.entry_date.desc())
    )
    positions = []
    for p in result.scalars().all():
        current = await yahoo.latest_close(db, p.symbol)
        positions.append(_position_dict(p, current))
    return {"summary": await _portfolio_summary(db, portfolio), "positions": positions}


@router.get("/portfolios/{portfolio_id}/history")
async def portfolio_history(portfolio_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Position).where(Position.portfolio_id == portfolio_id))
    return await equity_curve(db, list(result.scalars().all()))


@router.post("/portfolios/{portfolio_id}/positions", status_code=201)
async def add_position(portfolio_id: int, payload: PositionCreate,
                       db: AsyncSession = Depends(get_db)):
    portfolio = await db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio nicht gefunden")
    symbol = payload.symbol.strip().upper()
    try:
        await yahoo.ensure_asset(db, symbol)
        await yahoo.sync_ohlcv(db, symbol)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Symbol {symbol} nicht auflösbar: {e}")

    entry_price = payload.entry_price or await yahoo.latest_close(db, symbol)
    if entry_price is None:
        raise HTTPException(status_code=422, detail=f"Kein Kurs für {symbol} verfügbar")

    position = Position(
        portfolio_id=portfolio_id, symbol=symbol, quantity=payload.quantity,
        entry_price=entry_price, notes=payload.notes,
    )
    db.add(position)
    await db.commit()
    return {"id": str(position.id), "entry_price": entry_price, "ok": True}


@router.post("/positions/{position_id}/close")
async def close_position(position_id: uuid.UUID, payload: PositionClose,
                         db: AsyncSession = Depends(get_db)):
    position = await db.get(Position, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position nicht gefunden")
    if position.exit_date is not None:
        raise HTTPException(status_code=409, detail="Position ist bereits geschlossen")
    exit_price = payload.exit_price or await yahoo.latest_close(db, position.symbol)
    if exit_price is None:
        raise HTTPException(status_code=422, detail="Kein Kurs verfügbar — exit_price angeben")
    position.exit_price = exit_price
    position.exit_date = utcnow()
    await db.commit()
    return {"ok": True, "exit_price": exit_price}


@router.delete("/positions/{position_id}")
async def delete_position(position_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    position = await db.get(Position, position_id)
    if position:
        await db.delete(position)
        await db.commit()
    return {"ok": True}
