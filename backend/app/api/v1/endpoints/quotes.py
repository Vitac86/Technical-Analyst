from fastapi import APIRouter, HTTPException, Query

from app.schemas.quote import QuoteSnapshot
from app.services.market_data.moex_provider import MoexProvider


router = APIRouter()


@router.get("/moex", response_model=QuoteSnapshot)
async def get_moex_quote(
    ticker: str = Query(..., description="Instrument ticker (e.g. SBER)"),
    engine: str = Query("stock", description="MOEX engine (e.g. stock, currency, futures)"),
    market: str = Query("shares", description="MOEX market (e.g. shares, selt, forts)"),
    board: str = Query("TQBR", description="MOEX board (e.g. TQBR, CETS, RFUD)"),
) -> QuoteSnapshot:
    """Return a current market snapshot for a single MOEX instrument.

    Price fields are null when the market is closed or data is unavailable —
    this is a normal 200 response, not an error.  A 503 is returned only when
    the MOEX ISS API itself cannot be reached.
    """
    provider = MoexProvider()
    try:
        data = await provider.fetch_quote(
            ticker=ticker,
            engine=engine,
            market=market,
            board=board,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"MOEX ISS unavailable: {exc}",
        ) from exc
    return QuoteSnapshot(**data)
