from worldcup_arb.model import (
    Event,
    FixedOddsInstrument,
    Outcome,
    PredictionShareInstrument,
    analyze_event,
    event_from_mapping,
    optimize_for_budget,
)


def test_fixed_odds_three_way_arbitrage() -> None:
    event = Event(
        id="e1",
        title="three way",
        outcomes=(Outcome("H"), Outcome("D"), Outcome("A")),
        instruments=(
            FixedOddsInstrument(id="h", outcome="H", decimal_odds=3.3),
            FixedOddsInstrument(id="d", outcome="D", decimal_odds=3.4),
            FixedOddsInstrument(id="a", outcome="A", decimal_odds=3.5),
        ),
    )

    portfolio = analyze_event(event, 1000)

    assert portfolio.is_arbitrage
    assert round(portfolio.total_cost, 2) == 882.86
    assert round(portfolio.guaranteed_profit, 2) == 117.14


def test_prediction_share_fee_and_depth_are_used() -> None:
    instrument = PredictionShareInstrument.from_mapping(
        {
            "id": "pm",
            "type": "prediction_share",
            "outcome": "YES",
            "ask_levels": [{"price": 0.4, "size": 10}, {"price": 0.5, "size": 10}],
            "fee_rate": 0.03,
            "cost_fx_to_base": 7,
            "payout_fx_to_base": 7,
        }
    )

    quote = instrument.quote_for_payout(105)

    assert quote.stake_or_shares == 15
    assert quote.average_price == (10 * 0.4 + 5 * 0.5) / 15
    assert round(quote.cost, 4) == round(((10 * 0.4 + 5 * 0.5) + (10 * 0.03 * 0.4 * 0.6) + (5 * 0.03 * 0.5 * 0.5)) * 7, 4)


def test_model_selects_cheapest_source_per_outcome() -> None:
    event = event_from_mapping(
        {
            "id": "e2",
            "title": "mixed",
            "outcomes": [{"id": "A"}, {"id": "B"}],
            "instruments": [
                {"id": "a-expensive", "type": "fixed_odds", "outcome": "A", "decimal_odds": 1.5},
                {"id": "a-cheap", "type": "prediction_share", "outcome": "A", "ask_price": 0.4},
                {"id": "b", "type": "prediction_share", "outcome": "B", "ask_price": 0.45},
            ],
        }
    )

    portfolio = analyze_event(event, 100)

    assert [quote.instrument_id for quote in portfolio.quotes] == ["a-cheap", "b"]
    assert portfolio.is_arbitrage


def test_optimize_for_budget_keeps_total_cost_under_budget() -> None:
    event = Event(
        id="e3",
        title="binary",
        outcomes=(Outcome("Y"), Outcome("N")),
        instruments=(
            PredictionShareInstrument(id="y", outcome="Y", ask_price=0.45),
            PredictionShareInstrument(id="n", outcome="N", ask_price=0.45),
        ),
    )

    portfolio = optimize_for_budget(event, 90)

    assert portfolio.is_arbitrage
    assert portfolio.total_cost <= 90.000001
    assert round(portfolio.minimum_payout, 2) == 100.0


def test_optimize_for_budget_stops_at_profitable_depth() -> None:
    event = Event(
        id="e4",
        title="depth constrained",
        outcomes=(Outcome("Y"), Outcome("N")),
        instruments=(
            PredictionShareInstrument.from_mapping(
                {"id": "y", "type": "prediction_share", "outcome": "Y", "ask_levels": [{"price": 0.45, "size": 10}]}
            ),
            PredictionShareInstrument.from_mapping(
                {"id": "n", "type": "prediction_share", "outcome": "N", "ask_levels": [{"price": 0.45, "size": 10}]}
            ),
            FixedOddsInstrument(id="y-fixed", outcome="Y", decimal_odds=1.9),
            FixedOddsInstrument(id="n-fixed", outcome="N", decimal_odds=1.9),
        ),
    )

    portfolio = optimize_for_budget(event, 1000)

    assert portfolio.is_arbitrage
    assert round(portfolio.minimum_payout, 4) == 10
    assert portfolio.total_cost < 10


def test_fixed_odds_tax_threshold_after_stake_rounding() -> None:
    instrument = FixedOddsInstrument(
        id="taxed",
        outcome="A",
        decimal_odds=2.5005,
        stake_step=2,
        tax_rate=0.2,
        tax_threshold_base=10_000,
    )

    quote = instrument.quote_for_payout(10_000)

    assert quote.payout_if_win >= 10_000
    assert "tax_threshold_adjusted" in quote.notes


def test_optimize_for_budget_handles_minimum_profitable_scale() -> None:
    event = Event(
        id="e5",
        title="flat fees",
        outcomes=(Outcome("Y"), Outcome("N")),
        instruments=(
            PredictionShareInstrument(id="y", outcome="Y", ask_price=0.35, flat_fee_base=100),
            PredictionShareInstrument(id="n", outcome="N", ask_price=0.35, flat_fee_base=100),
        ),
    )

    portfolio = optimize_for_budget(event, 1000)

    assert portfolio.is_arbitrage
    assert portfolio.total_cost <= 1000.000001
    assert portfolio.minimum_payout > 300
