"""World Cup arbitrage calculator."""

from .model import (
    ArbitragePortfolio,
    Event,
    FixedOddsInstrument,
    Outcome,
    PredictionShareInstrument,
    analyze_event,
    optimize_for_budget,
)

__all__ = [
    "ArbitragePortfolio",
    "Event",
    "FixedOddsInstrument",
    "Outcome",
    "PredictionShareInstrument",
    "analyze_event",
    "optimize_for_budget",
]
