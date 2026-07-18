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


# Hongkong-Blue-Chips (Yahoo .HK) — Hang-Seng-nahes Universum. Bewusst
# HK-gelistet (kein US-ADR-Mix): saubere Daten, ein Benchmark (^HSI),
# eine Zeitzone/Währung. Statisch geseedet (der Wikipedia-Index-Refresh
# fasst nur die westlichen Segmente an), über die UI erweiterbar.
CHINA = [
    ("0700.HK", "Tencent"), ("9988.HK", "Alibaba"), ("3690.HK", "Meituan"),
    ("1299.HK", "AIA Group"), ("0939.HK", "China Construction Bank"),
    ("1398.HK", "ICBC"), ("0005.HK", "HSBC"), ("0941.HK", "China Mobile"),
    ("2318.HK", "Ping An"), ("0388.HK", "HKEX"), ("1810.HK", "Xiaomi"),
    ("9618.HK", "JD.com"), ("2020.HK", "ANTA Sports"), ("0883.HK", "CNOOC"),
    ("0857.HK", "PetroChina"), ("0386.HK", "Sinopec"), ("1211.HK", "BYD"),
    ("0016.HK", "Sun Hung Kai Properties"), ("0011.HK", "Hang Seng Bank"),
    ("2628.HK", "China Life"), ("0027.HK", "Galaxy Entertainment"),
    ("0001.HK", "CK Hutchison"), ("0002.HK", "CLP Holdings"),
    ("0003.HK", "HK & China Gas"), ("0006.HK", "Power Assets"),
    ("0012.HK", "Henderson Land"), ("0066.HK", "MTR"), ("0175.HK", "Geely"),
    ("0688.HK", "China Overseas Land"), ("0762.HK", "China Unicom"),
    ("0823.HK", "Link REIT"), ("1038.HK", "CK Infrastructure"),
    ("1093.HK", "CSPC Pharmaceutical"), ("1113.HK", "CK Asset"),
    ("1177.HK", "Sino Biopharmaceutical"), ("1928.HK", "Sands China"),
    ("2313.HK", "Shenzhou International"), ("2331.HK", "Li Ning"),
    ("2382.HK", "Sunny Optical"), ("3988.HK", "Bank of China"),
]


async def seed_universe(db: AsyncSession) -> None:
    """Legt fehlende Universum-Symbole an (idempotent)."""
    result = await db.execute(select(UniverseSymbol.symbol))
    existing = {row[0] for row in result.all()}
    added = 0
    for segment, entries in (("DAX", DAX), ("US", US), ("CRYPTO", CRYPTO),
                             ("CHINA", CHINA)):
        for symbol, name in entries:
            if symbol not in existing:
                db.add(UniverseSymbol(symbol=symbol, name=name, segment=segment))
                added += 1
    if added:
        await db.commit()
        logger.info("Screening-Universum ergänzt (%d Symbole)", added)
