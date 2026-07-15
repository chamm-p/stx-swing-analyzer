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
from app.models import Asset, Portfolio, Position, TradingPlatform, utcnow
from app.sources import yahoo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class AutoConfig(BaseModel):
    start_capital: float = Field(default=10000.0, gt=0)
    max_per_trade: float = Field(default=1000.0, gt=0)
    max_positions: int = Field(default=10, ge=1, le=50)
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    # Goldene Swing-Regeln (Default aus der globalen Config)
    risk_pct: float = Field(default=1.0, gt=0, le=10)   # 1%-Regel
    min_crv: float = Field(default=1.5, ge=0, le=10)    # Mindest-CRV
    use_screener: bool = True
    enabled: bool = True


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="real", pattern="^(real|trial|auto)$")
    platform_id: int | None = None
    # Budget/"Spielgeld" — aktiviert Cash-Führung auch für real/trial
    start_capital: float | None = Field(default=None, gt=0)
    config: AutoConfig | None = None


def _tracks_cash(portfolio: Portfolio) -> bool:
    """Cash-Führung: Auto immer; real/trial sobald Startkapital gesetzt."""
    return portfolio.kind == "auto" or bool((portfolio.config or {}).get("start_capital"))


class PositionCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    quantity: float = Field(gt=0)
    entry_price: float | None = Field(default=None, gt=0)
    notes: str | None = None


class PositionClose(BaseModel):
    exit_price: float | None = Field(default=None, gt=0)
    # None oder >= Bestand = Komplettverkauf; sonst Teilverkauf
    quantity: float | None = Field(default=None, gt=0)


class PortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    watch_enabled: bool | None = None
    platform_id: int | None = None  # -1 = Plattform entfernen
    start_capital: float | None = Field(default=None, gt=0)
    ibkr_sync: bool | None = None  # echtes Depot: IBKR-Bestände spiegeln
    config: AutoConfig | None = None


# ------------------------------------------------------------- Plattformen

class PlatformPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    fees: dict = Field(default_factory=dict)


@router.get("/platforms")
async def list_platforms(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TradingPlatform).order_by(TradingPlatform.id))
    return [{"id": p.id, "name": p.name, "fees": p.fees} for p in result.scalars().all()]


@router.post("/platforms", status_code=201)
async def create_platform(payload: PlatformPayload, db: AsyncSession = Depends(get_db)):
    platform = TradingPlatform(name=payload.name.strip(), fees=payload.fees)
    db.add(platform)
    await db.commit()
    return {"id": platform.id, "ok": True}


@router.put("/platforms/{platform_id}")
async def update_platform(platform_id: int, payload: PlatformPayload,
                          db: AsyncSession = Depends(get_db)):
    platform = await db.get(TradingPlatform, platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail="Plattform nicht gefunden")
    platform.name = payload.name.strip()
    platform.fees = payload.fees
    await db.commit()
    return {"ok": True}


@router.delete("/platforms/{platform_id}")
async def delete_platform(platform_id: int, db: AsyncSession = Depends(get_db)):
    platform = await db.get(TradingPlatform, platform_id)
    if platform:
        await db.delete(platform)  # Portfolios: platform_id -> NULL (FK)
        await db.commit()
    return {"ok": True}


@router.post("/portfolios/{portfolio_id}/promote")
async def promote_champion(portfolio_id: int, db: AsyncSession = Depends(get_db)):
    """Challenger → Champion: bewährte Strategie-Parameter global in die
    Live-Signallogik übernehmen (Screener, Pipeline, Discovery, Ziele).

    Bewusst manuell — das System empfiehlt, der User approved."""
    from app.analysis.scoring import set_champion
    from app.models import AppSetting

    pf = await db.get(Portfolio, portfolio_id)
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio nicht gefunden")
    strategy = (pf.config or {}).get("strategy") or {}
    allowed = {"threshold", "target_atr_factor", "stop_atr_factor"}
    new = {k: float(v) for k, v in strategy.items() if k in allowed and v is not None}
    if not new:
        raise HTTPException(status_code=422,
                            detail="Portfolio hat keine Challenger-Strategie (config.strategy)")

    row = await db.get(AppSetting, "strategy")
    old = dict(row.value or {}) if row else {}
    if row is None:
        db.add(AppSetting(key="strategy", value=new))
    else:
        row.value = new
    await db.commit()
    set_champion(new)  # Backend-Prozess sofort; Worker zieht binnen 20s nach
    logger.info("Champion befördert aus %s: %s (vorher: %s)", pf.name, new, old or "Defaults")
    return {"promoted_from": pf.name, "old": old or None, "new": new,
            "note": "Gilt ab sofort für Analysen/Screener; Worker übernimmt binnen 20 s."}


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
    value = invested = realized = fees_total = 0.0
    open_count = 0
    for p in positions:
        current = await yahoo.latest_close(db, p.symbol) if p.exit_date is None else None
        pv = position_value(p, current)
        fees_total += pv["fees"]
        if p.exit_date is None:
            open_count += 1
            invested += pv["invested"]
            if pv["value"] is not None:
                value += pv["value"]
        elif pv["pnl_abs"] is not None:
            realized += pv["pnl_abs"]
    platform = await db.get(TradingPlatform, portfolio.platform_id) if portfolio.platform_id else None

    # Wertänderung 1T/7T über die offenen Positionen (Batch aus Ohlcv).
    # Näherung: aktuelle Stückzahlen zu beiden Zeitpunkten (Trades innerhalb
    # des Fensters werden nicht zeitanteilig gerechnet); Positionen ohne
    # Kurshistorie bleiben außen vor. Cash dämpft den Prozentwert bewusst —
    # es ist die ehrliche Bewegung des Gesamtportfolios, nicht der Einzelkurse.
    change_1d = change_7d = None
    open_pos = [p for p in positions if p.exit_date is None]
    if open_pos:
        bars = await yahoo.recent_bars(db, [p.symbol for p in open_pos])
        now1 = prev1 = now7 = prev7 = 0.0
        for p in open_pos:
            b = bars.get(p.symbol) or []
            if not b or b[0][1] is None:
                continue
            latest = b[0][1]
            c1 = b[1][1] if len(b) >= 2 else None
            if c1:
                now1 += p.quantity * latest
                prev1 += p.quantity * c1
            c7 = yahoo.close_near(b, 7)
            if c7:
                now7 += p.quantity * latest
                prev7 += p.quantity * c7
        base_cash = portfolio.cash if _tracks_cash(portfolio) else 0.0
        if prev1:
            change_1d = round((now1 - prev1) / (prev1 + base_cash) * 100, 2)
        if prev7:
            change_7d = round((now7 - prev7) / (prev7 + base_cash) * 100, 2)

    out = {
        "id": portfolio.id, "name": portfolio.name, "kind": portfolio.kind,
        "ibkr_sync": bool((portfolio.config or {}).get("ibkr_sync")),
        "change_1d": change_1d, "change_7d": change_7d,
        "watch_enabled": portfolio.watch_enabled,
        "platform_id": portfolio.platform_id,
        "platform_name": platform.name if platform else None,
        "fees_total": round(fees_total, 2),
        "created_at": portfolio.created_at,
        "open_positions": open_count,
        "invested": round(invested, 2),
        "value": round(value, 2),
        "pnl_abs": round(value - invested, 2),
        "pnl_pct": round((value - invested) / invested * 100, 2) if invested else 0.0,
        "realized_pnl": round(realized, 2),
    }
    if _tracks_cash(portfolio):
        cfg = portfolio.config or {}
        start = cfg.get("start_capital") or 0.0
        total = value + portfolio.cash
        out.update({
            "cash": round(portfolio.cash, 2),
            "start_capital": start,
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
    portfolio = Portfolio(name=payload.name.strip(), kind=payload.kind,
                          platform_id=payload.platform_id)
    if payload.kind == "auto":
        cfg = payload.config or AutoConfig()
        if payload.start_capital:
            cfg.start_capital = payload.start_capital
        portfolio.config = cfg.model_dump()
        portfolio.cash = cfg.start_capital
    elif payload.start_capital:
        portfolio.config = {"start_capital": payload.start_capital}
        portfolio.cash = payload.start_capital
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
    if payload.platform_id is not None:
        portfolio.platform_id = None if payload.platform_id == -1 else payload.platform_id
    if payload.start_capital is not None:
        # Startkapital-Änderung: Cash um die Differenz anpassen
        cfg = dict(portfolio.config or {})
        old = cfg.get("start_capital") or 0.0
        cfg["start_capital"] = payload.start_capital
        portfolio.config = cfg
        portfolio.cash += payload.start_capital - old
    if payload.ibkr_sync is not None:
        if portfolio.kind != "real":
            raise HTTPException(status_code=422,
                                detail="IBKR-Sync nur für echte Portfolios")
        portfolio.config = {**(portfolio.config or {}), "ibkr_sync": payload.ibkr_sync}
    if payload.config is not None and portfolio.kind == "auto":
        merged = payload.config.model_dump()
        portfolio.config = {**(portfolio.config or {}), **merged}
    await db.commit()
    return {"ok": True}


@router.post("/portfolios/{portfolio_id}/ibkr-sync")
async def ibkr_sync_now(portfolio_id: int, db: AsyncSession = Depends(get_db)):
    """IBKR-Bestände sofort in dieses Portfolio spiegeln (read-only) —
    funktioniert auch als einmaliger Import ohne aktivierten Auto-Sync."""
    from app.broker.ibkr_sync import _reconcile, fetch_ibkr_state

    portfolio = await db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio nicht gefunden")
    if portfolio.kind != "real":
        raise HTTPException(status_code=422, detail="IBKR-Sync nur für echte Portfolios")
    try:
        state = await fetch_ibkr_state(db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=(
            f"IBKR-Gateway nicht erreichbar: {e} — läuft der ib-gateway-Container?"))
    stats = await _reconcile(db, portfolio, state)
    return {"ok": True, **stats,
            "ibkr_positions": len(state["positions"]), "cash": state.get("cash")}


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
    """Equity-Kurve + Benchmark (BENCHMARK_SYMBOL, auf Startwert normiert) —
    ohne Vergleichslinie ist eine Portfolio-Kurve nicht interpretierbar."""
    import pandas as pd

    from app.config import get_settings
    from app.services_redis import get_redis

    result = await db.execute(select(Position).where(Position.portfolio_id == portfolio_id))
    series = await equity_curve(db, list(result.scalars().all()))

    benchmark: list[dict] = []
    if series:
        bench_symbol = get_settings().benchmark_symbol
        # Benchmark-Kurse höchstens 1x täglich nachziehen
        if await get_redis().set(f"benchsync:{bench_symbol}", "1", nx=True, ex=86400):
            try:
                await yahoo.sync_ohlcv(db, bench_symbol)
            except Exception as e:
                logger.warning("Benchmark-Sync %s fehlgeschlagen: %s", bench_symbol, e)
        df = await yahoo.load_ohlcv_df(db, bench_symbol, days=len(series) + 40)
        if not df.empty:
            idx = pd.to_datetime([s["time"] for s in series], utc=True)
            closes = df["close"]
            closes = closes.reindex(closes.index.union(idx)).ffill().reindex(idx)
            valid = closes.dropna()
            if not valid.empty:
                base_close = float(valid.iloc[0])
                base_value = series[0]["value"]
                benchmark = [
                    {"time": s["time"], "value": round(base_value * float(c) / base_close, 2)}
                    for s, c in zip(series, closes)
                    if c == c  # NaN-Filter
                ]
    return {"series": series, "benchmark": benchmark,
            "benchmark_symbol": get_settings().benchmark_symbol}


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

    from app.analysis.fees import portfolio_fee
    asset = await db.get(Asset, symbol)
    fee = await portfolio_fee(db, portfolio, asset.currency if asset else None,
                              payload.quantity * entry_price, quantity=payload.quantity)

    position = Position(
        portfolio_id=portfolio_id, symbol=symbol, quantity=payload.quantity,
        entry_price=entry_price, notes=payload.notes, fee_buy=fee,
    )
    db.add(position)
    # Cash-geführte Portfolios: Kaufkosten inkl. Gebühr abbuchen
    if _tracks_cash(portfolio):
        portfolio.cash -= payload.quantity * entry_price + fee
    await db.commit()
    return {"id": str(position.id), "entry_price": entry_price, "fee": fee, "ok": True}


@router.post("/positions/{position_id}/close")
async def close_position(position_id: uuid.UUID, payload: PositionClose,
                         db: AsyncSession = Depends(get_db)):
    """Verkauf — komplett oder stückweise.

    Teilverkauf: Die verkaufte Menge wird als eigene, geschlossene
    Position abgespalten (Einstiegsdaten bleiben erhalten → korrektes
    realisiertes P/L), der Rest bleibt offen."""
    position = await db.get(Position, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position nicht gefunden")
    if position.exit_date is not None:
        raise HTTPException(status_code=409, detail="Position ist bereits geschlossen")
    exit_price = payload.exit_price or await yahoo.latest_close(db, position.symbol)
    if exit_price is None:
        raise HTTPException(status_code=422, detail="Kein Kurs verfügbar — exit_price angeben")

    sell_qty = payload.quantity
    partial = sell_qty is not None and sell_qty < position.quantity
    now = utcnow()

    from app.analysis.fees import portfolio_fee
    portfolio = await db.get(Portfolio, position.portfolio_id)
    asset = await db.get(Asset, position.symbol)
    sold_quantity = sell_qty if partial else position.quantity
    fee_sell = await portfolio_fee(db, portfolio, asset.currency if asset else None,
                                   exit_price * sold_quantity, quantity=sold_quantity)

    if partial:
        # Kaufgebühr anteilig auf den verkauften Teil umlegen
        fraction = sell_qty / position.quantity
        fee_buy_share = round((position.fee_buy or 0.0) * fraction, 2)
        sold = Position(
            portfolio_id=position.portfolio_id, symbol=position.symbol,
            quantity=sell_qty, entry_price=position.entry_price,
            entry_date=position.entry_date, exit_price=exit_price, exit_date=now,
            source=position.source, signal_id=position.signal_id,
            horizon_days=position.horizon_days,
            target_price=position.target_price, stop_price=position.stop_price,
            fee_buy=fee_buy_share, fee_sell=fee_sell,
            notes=f"{position.notes or ''} | Teilverkauf {sell_qty} von {position.quantity}".strip(" |"),
        )
        db.add(sold)
        position.quantity = round(position.quantity - sell_qty, 6)
        position.fee_buy = round((position.fee_buy or 0.0) - fee_buy_share, 2)
    else:
        position.exit_price = exit_price
        position.exit_date = now
        position.fee_sell = fee_sell

    # Cash-geführte Portfolios: Verkaufserlös abzüglich Gebühr gutschreiben
    if portfolio and _tracks_cash(portfolio):
        portfolio.cash += exit_price * sold_quantity - fee_sell

    await db.commit()
    return {"ok": True, "exit_price": exit_price, "sold_quantity": sold_quantity,
            "fee": fee_sell, "remaining": position.quantity if partial else 0}


@router.post("/positions/{position_id}/reopen")
async def reopen_position(position_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Verkauf rückgängig machen.

    Teilverkaufs-Scheiben werden mit der offenen Rest-Position wieder
    verschmolzen (Menge + Kaufgebühr zurück), Komplettverkäufe einfach
    wieder geöffnet. Cash-geführte Portfolios buchen den Erlös zurück."""
    position = await db.get(Position, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position nicht gefunden")
    if position.exit_date is None:
        raise HTTPException(status_code=409, detail="Position ist offen — nichts rückgängig zu machen")

    proceeds = (position.exit_price or 0.0) * position.quantity - (position.fee_sell or 0.0)
    portfolio = await db.get(Portfolio, position.portfolio_id)

    merged_into = None
    if "Teilverkauf" in (position.notes or ""):
        sibling = await db.scalar(
            select(Position).where(
                Position.portfolio_id == position.portfolio_id,
                Position.symbol == position.symbol,
                Position.exit_date.is_(None),
                Position.entry_date == position.entry_date,
                Position.entry_price == position.entry_price,
            ).limit(1)
        )
        if sibling is not None:
            sibling.quantity = round(sibling.quantity + position.quantity, 6)
            sibling.fee_buy = round((sibling.fee_buy or 0.0) + (position.fee_buy or 0.0), 2)
            merged_into = str(sibling.id)
            await db.delete(position)

    if merged_into is None:
        position.exit_price = None
        position.exit_date = None
        position.fee_sell = 0.0

    if portfolio and _tracks_cash(portfolio):
        portfolio.cash -= proceeds

    await db.commit()
    return {"ok": True, "merged_into": merged_into}


@router.delete("/positions/{position_id}")
async def delete_position(position_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    position = await db.get(Position, position_id)
    if position:
        await db.delete(position)
        await db.commit()
    return {"ok": True}
