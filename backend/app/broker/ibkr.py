"""IBKR-Anbindung über die Client-Portal-Web-API (REST, OAuth 1.0a).

Headless und Gateway-los: Die App signiert Requests direkt gegen
api.ibkr.com (ibind-Bibliothek). Einmalige Einrichtung im IBKR-
Self-Service-OAuth-Portal: RSA-Schlüsselpaare + DH-Params erzeugen,
öffentliche Schlüssel hochladen, Consumer-Key + Access-Token in den
App-Einstellungen hinterlegen. Die privaten Schlüssel liegen als PEM
unter ``settings.ibkr_keys_dir`` (Volume, nie im Git).

Sicherheitsmodell unverändert: Orders nur mit ``trading_enabled`` in
den Settings UND explizitem confirm im Request; sonst ist die
Verbindung faktisch read-only (wir rufen nur Portfolio-Endpunkte).

ibind ist synchron (requests) → alle Aufrufe laufen im Thread-Executor.
"""

import asyncio
import base64
import logging
import re
import time
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_conn_lock = asyncio.Lock()


async def ibkr_config(db: AsyncSession) -> dict:
    from app.services_settings import load_settings
    return await load_settings(db, "ibkr")


def _trading_enabled(cfg: dict) -> bool:
    return str(cfg.get("trading_enabled") or "").strip().lower() in ("1", "true", "yes", "ja")


def dh_prime_from_pem(path: str) -> str:
    """DH-Prime (hex) aus dhparam.pem — minimaler DER-Parser.

    dhparam ist ASN.1: SEQUENCE { prime INTEGER, generator INTEGER }."""
    body = re.sub(r"-----[A-Z ]+-----|\s", "", Path(path).read_text())
    der = base64.b64decode(body)

    def read_len(buf: bytes, i: int) -> tuple[int, int]:
        first = buf[i]
        if first < 0x80:
            return first, i + 1
        n = first & 0x7F
        return int.from_bytes(buf[i + 1:i + 1 + n], "big"), i + 1 + n

    if der[0] != 0x30:
        raise ValueError("dhparam.pem: kein ASN.1-SEQUENCE")
    _, i = read_len(der, 1)
    if der[i] != 0x02:
        raise ValueError("dhparam.pem: Prime-INTEGER nicht gefunden")
    plen, i = read_len(der, i + 1)
    prime = der[i:i + plen].lstrip(b"\x00")
    return prime.hex()


def _missing_config(cfg: dict) -> list[str]:
    from app.config import get_settings

    keys_dir = Path(get_settings().ibkr_keys_dir)
    missing = [k for k in ("consumer_key", "access_token", "access_token_secret")
               if not str(cfg.get(k) or "").strip()]
    for fname in ("private_signature.pem", "private_encryption.pem", "dhparam.pem"):
        if not (keys_dir / fname).exists():
            missing.append(str(keys_dir / fname))
    return missing


def _make_client(cfg: dict):
    """IbkrClient mit OAuth-Konfiguration (synchron aufzurufen)."""
    from ibind import IbkrClient
    from ibind.oauth.oauth1a import OAuth1aConfig

    from app.config import get_settings

    keys_dir = Path(get_settings().ibkr_keys_dir)
    oauth = OAuth1aConfig(
        access_token=str(cfg["access_token"]).strip(),
        access_token_secret=str(cfg["access_token_secret"]).strip(),
        consumer_key=str(cfg["consumer_key"]).strip(),
        dh_prime=dh_prime_from_pem(str(keys_dir / "dhparam.pem")),
        encryption_key_fp=str(keys_dir / "private_encryption.pem"),
        signature_key_fp=str(keys_dir / "private_signature.pem"),
        # Kein Hintergrund-Tickler: Client lebt nur für einen Aufruf
        maintain_oauth=False,
        shutdown_oauth=False,
    )
    return IbkrClient(
        account_id=str(cfg.get("account") or "").strip() or None,
        use_oauth=True,
        oauth_config=oauth,
        # Kein atexit/Signal-Handling — wir laufen im Thread-Executor
        auto_register_shutdown=False,
        timeout=20,
    )


async def _run(cfg: dict, fn):
    """Client bauen, fn(client) im Thread ausführen, Fehler durchreichen."""
    missing = _missing_config(cfg)
    if missing:
        raise RuntimeError(
            "IBKR-OAuth unvollständig — es fehlt: " + ", ".join(missing)
            + " (Einstellungen → IBKR bzw. Schlüsseldateien auf dem Server)")

    def call():
        client = _make_client(cfg)
        return fn(client)

    async with _conn_lock:
        return await asyncio.to_thread(call)


def _account_id(client, cfg: dict) -> str:
    acct = str(cfg.get("account") or "").strip()
    if acct:
        return acct
    accounts = client.portfolio_accounts().data or []
    if not accounts:
        raise RuntimeError("Kein IBKR-Konto in der OAuth-Session gefunden")
    return accounts[0]["accountId"]


# ---------------------------------------------------------------- Status

async def status(db: AsyncSession) -> dict:
    """Verbindungstest: Konto-Kennzahlen + offene IBKR-Positionen."""
    cfg = await ibkr_config(db)

    def fetch(client) -> dict:
        acct = _account_id(client, cfg)
        summary = client.portfolio_summary(account_id=acct).data or {}

        def val(key):
            entry = summary.get(key) or {}
            return {"value": entry.get("amount"), "currency": entry.get("currency")}

        positions = []
        page = 0
        while True:
            batch = client.positions(account_id=acct, page=page).data or []
            for p in batch:
                positions.append({
                    "symbol": p.get("ticker") or p.get("contractDesc"),
                    "conid": p.get("conid"),
                    "exchange": p.get("listingExchange"),
                    "currency": p.get("currency"),
                    "asset_class": p.get("assetClass"),
                    "quantity": p.get("position"),
                    "avg_cost": round(float(p.get("avgCost") or 0), 4),
                })
            if len(batch) < 100:
                break
            page += 1
        return {
            "connected": True,
            "api": "Web-API (OAuth)",
            "trading_enabled": _trading_enabled(cfg),
            "accounts": [acct],
            "summary": {
                "NetLiquidation": val("netliquidation"),
                "TotalCashValue": val("totalcashvalue"),
                "BuyingPower": val("buyingpower"),
            },
            "positions": positions,
        }

    return await _run(cfg, fetch)


# ---------------------------------------------------------------- Orders

def _conid_for(client, symbol: str, currency: str | None) -> int:
    """Yahoo-Symbol → IBKR-conid. US (SMART/USD) und XETRA (.DE)."""
    from ibind.client.ibkr_utils import StockQuery

    sym = symbol.upper().strip()
    if sym.endswith("-USD"):
        raise ValueError(f"Krypto ({sym}) läuft nicht über die Aktien-API")
    if sym.endswith(".DE"):
        query = StockQuery(sym[:-3], contract_conditions={"exchange": "IBIS"})
    else:
        query = StockQuery(sym.replace("-", " "),
                           contract_conditions={"isUS": True})
    result = client.stock_conid_by_symbol(query).data or {}
    conid = result.get(query.symbol)
    if not conid:
        raise ValueError(f"Kein IBKR-Kontrakt für {symbol} gefunden")
    return int(conid)


# Standard-Bestätigungsfragen, die wir bewusst durchwinken — alles
# Informations-Prompts; ohne Echtzeit-Marktdaten-Abo fragt IBKR z.B.
# bei jeder Order nach (MISSING_MARKET_DATA).
def _default_answers() -> dict:
    from ibind.client.ibkr_utils import QuestionType

    return {
        QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
        QuestionType.MISSING_MARKET_DATA: True,
        QuestionType.TRIGGER_AND_FILL: True,
        QuestionType.STOP_ORDER_RISKS: True,
        QuestionType.CASH_QUANTITY: True,
        QuestionType.DISRUPTIVE_ORDERS: True,
        QuestionType.MANDATORY_CAP_PRICE: True,
    }


async def place_order(db: AsyncSession, symbol: str, side: str, quantity: float,
                      order_type: str = "MKT", limit_price: float | None = None,
                      take_profit: float | None = None,
                      stop_loss: float | None = None,
                      currency: str | None = None,
                      wait_seconds: float = 12.0) -> dict:
    """Order über die Web-API; mit take_profit/stop_loss als Bracket."""
    from ibind.client.ibkr_utils import make_order_request

    cfg = await ibkr_config(db)
    if not _trading_enabled(cfg):
        raise PermissionError("IBKR-Trading ist deaktiviert — in den Einstellungen "
                              "„Orders erlauben“ aktivieren")
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side muss BUY oder SELL sein")
    if quantity <= 0:
        raise ValueError("quantity muss > 0 sein")
    if order_type == "LMT" and not limit_price:
        raise ValueError("limit_price fehlt für LMT-Order")

    def submit(client) -> dict:
        acct = _account_id(client, cfg)
        conid = _conid_for(client, symbol, currency)
        coid = f"stx-{uuid.uuid4().hex[:18]}"
        requests = [make_order_request(
            conid=conid, side=side, quantity=quantity,
            order_type=order_type, acct_id=acct, coid=coid,
            price=limit_price if order_type == "LMT" else None,
        )]
        exit_side = "SELL" if side == "BUY" else "BUY"
        if take_profit:
            requests.append(make_order_request(
                conid=conid, side=exit_side, quantity=quantity,
                order_type="LMT", acct_id=acct, price=take_profit,
                parent_id=coid))
        if stop_loss:
            requests.append(make_order_request(
                conid=conid, side=exit_side, quantity=quantity,
                order_type="STP", acct_id=acct, aux_price=stop_loss,
                parent_id=coid))

        result = client.place_order(requests, answers=_default_answers(),
                                    account_id=acct).data
        entries = result if isinstance(result, list) else [result]
        order_id = next((e.get("order_id") for e in entries
                         if isinstance(e, dict) and e.get("order_id")), None)

        # Kurz auf den Fill warten (Market-Orders füllen i.d.R. sofort)
        status_txt, avg_price, filled = "Submitted", None, 0.0
        deadline = time.monotonic() + wait_seconds
        while order_id and time.monotonic() < deadline:
            try:
                st = client.order_status(order_id).data or {}
            except Exception:
                break
            status_txt = st.get("order_status") or status_txt
            avg_price = st.get("average_price") or avg_price
            filled = st.get("cum_fill") or filled
            if status_txt in ("Filled", "Cancelled", "Rejected"):
                break
            time.sleep(0.5)
        return {
            "order_id": order_id,
            "status": status_txt,
            "filled": float(filled or 0),
            "avg_fill_price": float(avg_price) if avg_price else None,
            "commission": None,  # liefert die Web-API erst im Abrechnungslauf
            "bracket_orders": len(requests) - 1,
        }

    return await _run(cfg, submit)
