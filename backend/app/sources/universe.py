"""Default-Screening-Universum: DAX + große US-Titel (Yahoo-Notation).

Bewusst unabhängig von der Watchlist — der Screener soll Kandidaten
AUSSERHALB des eigenen Bias liefern. Über die API/UI erweiterbar.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UniverseSymbol

logger = logging.getLogger(__name__)

DAX = [
    ("ADS.DE", "Adidas"), ("AIR.DE", "Airbus"), ("ALV.DE", "Allianz"),
    ("BAS.DE", "BASF"), ("BAYN.DE", "Bayer"), ("BMW.DE", "BMW"),
    ("CBK.DE", "Commerzbank"), ("CON.DE", "Continental"), ("DBK.DE", "Deutsche Bank"),
    ("DB1.DE", "Deutsche Börse"), ("DHL.DE", "DHL Group"), ("DTE.DE", "Deutsche Telekom"),
    ("EOAN.DE", "E.ON"), ("FRE.DE", "Fresenius"), ("HEI.DE", "Heidelberg Materials"),
    ("HEN3.DE", "Henkel"), ("IFX.DE", "Infineon"), ("MBG.DE", "Mercedes-Benz"),
    ("MRK.DE", "Merck KGaA"), ("MUV2.DE", "Munich Re"), ("RWE.DE", "RWE"),
    ("SAP.DE", "SAP"), ("SIE.DE", "Siemens"), ("VOW3.DE", "Volkswagen"),
    ("VNA.DE", "Vonovia"), ("ZAL.DE", "Zalando"),
]

US = [
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"),
    ("GOOGL", "Alphabet"), ("AMZN", "Amazon"), ("META", "Meta Platforms"),
    ("TSLA", "Tesla"), ("AVGO", "Broadcom"), ("BRK-B", "Berkshire Hathaway"),
    ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("MA", "Mastercard"),
    ("UNH", "UnitedHealth"), ("XOM", "Exxon Mobil"), ("JNJ", "Johnson & Johnson"),
    ("WMT", "Walmart"), ("PG", "Procter & Gamble"), ("HD", "Home Depot"),
    ("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("COST", "Costco"),
    ("ORCL", "Oracle"), ("BAC", "Bank of America"), ("CRM", "Salesforce"),
    ("AMD", "AMD"), ("NFLX", "Netflix"), ("DIS", "Disney"),
    ("ADBE", "Adobe"), ("CSCO", "Cisco"), ("INTC", "Intel"),
    ("QCOM", "Qualcomm"), ("TXN", "Texas Instruments"), ("IBM", "IBM"),
    ("GE", "GE Aerospace"), ("CAT", "Caterpillar"), ("NKE", "Nike"),
    ("MCD", "McDonald's"), ("SBUX", "Starbucks"), ("VZ", "Verizon"),
    ("PFE", "Pfizer"), ("MRK", "Merck & Co"), ("ABBV", "AbbVie"),
    ("LLY", "Eli Lilly"), ("TMO", "Thermo Fisher"), ("ABT", "Abbott"),
    ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley"), ("AXP", "American Express"),
    ("BA", "Boeing"), ("UPS", "UPS"), ("RTX", "RTX"),
    ("HON", "Honeywell"), ("UNP", "Union Pacific"), ("LOW", "Lowe's"),
    ("CVX", "Chevron"), ("COP", "ConocoPhillips"), ("MDT", "Medtronic"),
    ("BMY", "Bristol-Myers Squibb"), ("AMGN", "Amgen"), ("GILD", "Gilead"),
    ("ISRG", "Intuitive Surgical"), ("NOW", "ServiceNow"), ("UBER", "Uber"),
    ("PYPL", "PayPal"), ("PLTR", "Palantir"), ("SHOP", "Shopify"),
]


CRYPTO = [
    ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("XRP-USD", "XRP"),
    ("BNB-USD", "BNB"), ("SOL-USD", "Solana"), ("DOGE-USD", "Dogecoin"),
    ("ADA-USD", "Cardano"), ("TRX-USD", "TRON"), ("AVAX-USD", "Avalanche"),
    ("LINK-USD", "Chainlink"), ("DOT-USD", "Polkadot"), ("LTC-USD", "Litecoin"),
    ("BCH-USD", "Bitcoin Cash"), ("XLM-USD", "Stellar"), ("NEAR-USD", "NEAR Protocol"),
    ("ATOM-USD", "Cosmos"), ("XMR-USD", "Monero"), ("ETC-USD", "Ethereum Classic"),
    ("FIL-USD", "Filecoin"), ("AAVE-USD", "Aave"),
]


async def seed_universe(db: AsyncSession) -> None:
    """Legt fehlende Universum-Symbole an (idempotent)."""
    result = await db.execute(select(UniverseSymbol.symbol))
    existing = {row[0] for row in result.all()}
    added = 0
    for segment, entries in (("DAX", DAX), ("US", US), ("CRYPTO", CRYPTO)):
        for symbol, name in entries:
            if symbol not in existing:
                db.add(UniverseSymbol(symbol=symbol, name=name, segment=segment))
                added += 1
    if added:
        await db.commit()
        logger.info("Screening-Universum ergänzt (%d Symbole)", added)
