from typing import Any


class SignalEngine:
    """Future engine for combining indicators into trading research signals."""

    def generate_signals(self) -> list[dict[str, Any]]:
        """Return generated signals.

        The first implementation should read normalized candles and calculated
        indicators, then persist reproducible signal records.
        """
        return []
