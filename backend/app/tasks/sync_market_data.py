from app.services.market_data.moex_provider import MoexProvider


async def sync_market_data() -> None:
    """Future local task for syncing instruments and candles from MOEX."""
    provider = MoexProvider()
    await provider.fetch_instruments()
