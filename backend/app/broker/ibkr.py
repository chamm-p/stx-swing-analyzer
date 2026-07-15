"""IBKR-Anbindung über das IB Gateway (TWS-API, ib_async).

Architektur: Das Gateway (Docker-Service ``ib-gateway``) hält die
IBKR-Session — Zugangsdaten liegen als Env beim Gateway-Container, NICHT
in der App. Die App verbindet sich nur mit dem API-Socket (Docker-intern,
Port 4004 Paper / 4003 Live) und ist per Default read-only: Orders gehen
erst raus, wenn ``trading_enabled`` in den Settings aktiv ist UND der
Aufruf explizit bestätigt wurde.

Verbindungen sind kurzlebig (connect → Aktion → disconnect) und
serialisiert — die TWS-API mag keine parallelen Sessions mit derselben
Client-ID.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_conn_lock = asyncio.Lock()


async def ibkr_config(db: AsyncSession) -> dict:
    from app.services_settings import load_settings
    return await load_settings(db, "ibkr")


def contract_for(symbol: str, currency: str | None = None):
    """Yahoo-Symbol → IBKR-Kontrakt. Deckt US (SMART/USD, Klassen-Suffix
    mit Leerzeichen: BRK-B → 'BRK B') und XETRA (.DE → SMART/EUR, IBIS) ab."""
    from ib_async import Stock

    sym = symbol.upper().strip()
    if sym.endswith("-USD"):
        raise ValueError("Krypto-Orders laufen nicht über das Aktien-Gateway "
                         f"({sym}) — bitte direkt bei der Börse handeln")
    if sym.endswith(".DE"):
        return Stock(sym[:-3], "SMART", "EUR", primaryExchange="IBIS")
    if "." in sym:
        raise ValueError(f"Börsen-Suffix {sym} ist noch nicht gemappt — "
                         "bitte Symbol/Exchange prüfen")
    return Stock(sym.replace("-", " "), "SMART", currency or "USD")


async def _connect(cfg: dict):
    from ib_async import IB

    ib = IB()
    await ib.connectAsync(
        host=str(cfg.get("host") or "ib-gateway"),
        port=int(cfg.get("port") or 4004),
        clientId=int(cfg.get("client_id") or 17),
        timeout=10,
        readonly=not _trading_enabled(cfg),
    )
    return ib


def _trading_enabled(cfg: dict) -> bool:
    return str(cfg.get("trading_enabled") or "").strip().lower() in ("1", "true", "yes", "ja")


async def status(db: AsyncSession) -> dict:
    """Verbindungstest + Konto-Überblick + offene IBKR-Positionen."""
    cfg = await ibkr_config(db)
    async with _conn_lock:
        ib = await _connect(cfg)
        try:
            account = str(cfg.get("account") or "") or None
            summary = await ib.accountSummaryAsync(account or "")
            wanted = {"NetLiquidation", "TotalCashValue", "BuyingPower",
                      "UnrealizedPnL", "RealizedPnL"}
            values = {row.tag: {"value": row.value, "currency": row.currency}
                      for row in summary if row.tag in wanted}
            positions = [{
                "symbol": p.contract.symbol, "exchange": p.contract.primaryExchange,
                "currency": p.contract.currency, "quantity": p.position,
                "avg_cost": round(p.avgCost, 4),
            } for p in ib.positions(account or "")]
            return {
                "connected": True,
                "server_version": ib.client.serverVersion(),
                "trading_enabled": _trading_enabled(cfg),
                "accounts": ib.managedAccounts(),
                "summary": values,
                "positions": positions,
            }
        finally:
            ib.disconnect()


async def place_order(db: AsyncSession, symbol: str, side: str, quantity: float,
                      order_type: str = "MKT", limit_price: float | None = None,
                      take_profit: float | None = None,
                      stop_loss: float | None = None,
                      currency: str | None = None,
                      wait_seconds: float = 12.0) -> dict:
    """Order platzieren; mit take_profit/stop_loss als Bracket (OCA).

    Liefert Order-Status und (falls binnen wait_seconds gefüllt) den
    durchschnittlichen Fill-Preis plus IBKR-Kommission."""
    from ib_async import LimitOrder, MarketOrder, StopOrder

    cfg = await ibkr_config(db)
    if not _trading_enabled(cfg):
        raise PermissionError("IBKR-Trading ist deaktiviert — in den Einstellungen "
                              "„Orders erlauben“ aktivieren")
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side muss BUY oder SELL sein")
    if quantity <= 0:
        raise ValueError("quantity muss > 0 sein")

    contract = contract_for(symbol, currency)
    async with _conn_lock:
        ib = await _connect(cfg)
        try:
            await ib.qualifyContractsAsync(contract)
            account = str(cfg.get("account") or "") or ""

            if order_type == "LMT":
                if not limit_price:
                    raise ValueError("limit_price fehlt für LMT-Order")
                parent = LimitOrder(side, quantity, limit_price)
            else:
                parent = MarketOrder(side, quantity)
            if account:
                parent.account = account

            trades = []
            if take_profit or stop_loss:
                # Bracket: Parent + verknüpfte Exits (One-Cancels-All)
                parent.transmit = False
                parent_trade = ib.placeOrder(contract, parent)
                trades.append(parent_trade)
                exit_side = "SELL" if side == "BUY" else "BUY"
                if take_profit:
                    tp = LimitOrder(exit_side, quantity, take_profit)
                    tp.parentId = parent.orderId
                    tp.transmit = not stop_loss
                    if account:
                        tp.account = account
                    trades.append(ib.placeOrder(contract, tp))
                if stop_loss:
                    sl = StopOrder(exit_side, quantity, stop_loss)
                    sl.parentId = parent.orderId
                    sl.transmit = True
                    if account:
                        sl.account = account
                    trades.append(ib.placeOrder(contract, sl))
            else:
                trades.append(ib.placeOrder(contract, parent))

            main_trade = trades[0]
            deadline = asyncio.get_event_loop().time() + wait_seconds
            while (asyncio.get_event_loop().time() < deadline
                   and not main_trade.isDone()):
                await asyncio.sleep(0.5)

            fills = main_trade.fills
            commission = sum(
                f.commissionReport.commission for f in fills
                if f.commissionReport and f.commissionReport.commission
            ) or None
            return {
                "order_id": main_trade.order.orderId,
                "status": main_trade.orderStatus.status,
                "filled": main_trade.orderStatus.filled,
                "avg_fill_price": main_trade.orderStatus.avgFillPrice or None,
                "commission": round(commission, 2) if commission else None,
                "bracket_orders": len(trades) - 1,
            }
        finally:
            ib.disconnect()
