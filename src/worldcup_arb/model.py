from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, isfinite
from typing import Any, Mapping, Protocol


class QuoteError(ValueError):
    """Raised when an instrument cannot quote the requested payout."""


@dataclass(frozen=True)
class Outcome:
    id: str
    label: str | None = None


@dataclass(frozen=True)
class AskLevel:
    price: float
    size: float

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AskLevel":
        return cls(price=_positive_float(raw["price"], "price"), size=_positive_float(raw["size"], "size"))


@dataclass(frozen=True)
class Quote:
    instrument_id: str
    source: str
    outcome: str
    label: str
    target_payout: float
    payout_if_win: float
    cost: float
    stake_or_shares: float
    unit: str
    average_price: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def cost_per_payout(self) -> float:
        return self.cost / self.payout_if_win


class Instrument(Protocol):
    id: str
    source: str
    outcome: str
    label: str

    def quote_for_payout(self, target_payout: float) -> Quote:
        ...


@dataclass(frozen=True)
class FixedOddsInstrument:
    id: str
    outcome: str
    decimal_odds: float
    source: str = "Sporttery"
    label: str = ""
    cost_fx_to_base: float = 1.0
    payout_fx_to_base: float = 1.0
    stake_step: float = 0.0
    min_stake: float = 0.0
    max_stake: float | None = None
    tax_rate: float = 0.0
    tax_threshold_base: float | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FixedOddsInstrument":
        return cls(
            id=str(raw["id"]),
            outcome=str(raw["outcome"]),
            decimal_odds=_positive_float(raw["decimal_odds"], "decimal_odds"),
            source=str(raw.get("source", "Sporttery")),
            label=str(raw.get("label", raw.get("id", ""))),
            cost_fx_to_base=_positive_float(raw.get("cost_fx_to_base", 1.0), "cost_fx_to_base"),
            payout_fx_to_base=_positive_float(raw.get("payout_fx_to_base", 1.0), "payout_fx_to_base"),
            stake_step=_non_negative_float(raw.get("stake_step", 0.0), "stake_step"),
            min_stake=_non_negative_float(raw.get("min_stake", 0.0), "min_stake"),
            max_stake=_optional_positive_float(raw.get("max_stake"), "max_stake"),
            tax_rate=_rate(raw.get("tax_rate", 0.0), "tax_rate"),
            tax_threshold_base=_optional_positive_float(raw.get("tax_threshold_base"), "tax_threshold_base"),
        )

    def quote_for_payout(self, target_payout: float) -> Quote:
        target_payout = _positive_float(target_payout, "target_payout")
        gross_needed_base = self._gross_needed_for_net(target_payout)
        notes: list[str] = []
        stake, gross_payout_base, payout_if_win = self._sized_stake(gross_needed_base, notes)
        if payout_if_win + 1e-9 < target_payout and self.tax_rate:
            notes.append("tax_threshold_adjusted")
            gross_needed_base = target_payout / (1.0 - self.tax_rate)
            stake, gross_payout_base, payout_if_win = self._sized_stake(gross_needed_base, notes)
        if payout_if_win + 1e-9 < target_payout:
            raise QuoteError(f"{self.id} cannot reach target payout {target_payout:.6f}")
        cost = stake * self.cost_fx_to_base

        return Quote(
            instrument_id=self.id,
            source=self.source,
            outcome=self.outcome,
            label=self.label or self.id,
            target_payout=target_payout,
            payout_if_win=payout_if_win,
            cost=cost,
            stake_or_shares=stake,
            unit="stake",
            average_price=None,
            notes=tuple(notes),
        )

    def _gross_needed_for_net(self, target_payout: float) -> float:
        if not self.tax_rate or self.tax_threshold_base is None:
            return target_payout
        if target_payout <= self.tax_threshold_base:
            return target_payout
        return target_payout / (1.0 - self.tax_rate)

    def _net_after_tax(self, gross_payout_base: float) -> float:
        if not self.tax_rate or self.tax_threshold_base is None:
            return gross_payout_base
        if gross_payout_base <= self.tax_threshold_base + 1e-9:
            return gross_payout_base
        return gross_payout_base * (1.0 - self.tax_rate)

    def _sized_stake(self, gross_needed_base: float, notes: list[str]) -> tuple[float, float, float]:
        stake = gross_needed_base / (self.decimal_odds * self.payout_fx_to_base)
        if self.min_stake and stake < self.min_stake:
            stake = self.min_stake
            _append_once(notes, "raised_to_min_stake")
        if self.stake_step:
            stake = ceil(stake / self.stake_step - 1e-12) * self.stake_step
            _append_once(notes, "rounded_to_stake_step")
        if self.max_stake is not None and stake > self.max_stake + 1e-9:
            raise QuoteError(f"{self.id} requires stake {stake:.6f}, above max_stake {self.max_stake:.6f}")
        gross_payout_base = stake * self.decimal_odds * self.payout_fx_to_base
        payout_if_win = self._net_after_tax(gross_payout_base)
        return stake, gross_payout_base, payout_if_win


@dataclass(frozen=True)
class PredictionShareInstrument:
    id: str
    outcome: str
    source: str = "Polymarket"
    label: str = ""
    ask_levels: tuple[AskLevel, ...] = field(default_factory=tuple)
    ask_price: float | None = None
    max_shares: float | None = None
    fee_rate: float = 0.0
    cost_fx_to_base: float = 1.0
    payout_fx_to_base: float = 1.0
    slippage_bps: float = 0.0
    extra_cost_rate: float = 0.0
    flat_fee_base: float = 0.0
    min_shares: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PredictionShareInstrument":
        ask_levels = tuple(AskLevel.from_mapping(level) for level in raw.get("ask_levels", []))
        ask_price = raw.get("ask_price")
        return cls(
            id=str(raw["id"]),
            outcome=str(raw["outcome"]),
            source=str(raw.get("source", "Polymarket")),
            label=str(raw.get("label", raw.get("id", ""))),
            ask_levels=ask_levels,
            ask_price=None if ask_price is None else _probability(ask_price, "ask_price"),
            max_shares=_optional_positive_float(raw.get("max_shares"), "max_shares"),
            fee_rate=_rate(raw.get("fee_rate", 0.0), "fee_rate"),
            cost_fx_to_base=_positive_float(raw.get("cost_fx_to_base", 1.0), "cost_fx_to_base"),
            payout_fx_to_base=_positive_float(raw.get("payout_fx_to_base", 1.0), "payout_fx_to_base"),
            slippage_bps=_non_negative_float(raw.get("slippage_bps", 0.0), "slippage_bps"),
            extra_cost_rate=_rate(raw.get("extra_cost_rate", 0.0), "extra_cost_rate"),
            flat_fee_base=_non_negative_float(raw.get("flat_fee_base", 0.0), "flat_fee_base"),
            min_shares=_non_negative_float(raw.get("min_shares", 0.0), "min_shares"),
        )

    def quote_for_payout(self, target_payout: float) -> Quote:
        target_payout = _positive_float(target_payout, "target_payout")
        requested_shares = target_payout / self.payout_fx_to_base
        shares = max(requested_shares, self.min_shares)
        if self.max_shares is not None and shares > self.max_shares + 1e-9:
            raise QuoteError(f"{self.id} requires {shares:.6f} shares, above max_shares {self.max_shares:.6f}")

        remaining = shares
        notional = 0.0
        fees = 0.0
        if self.ask_levels:
            levels = sorted(self.ask_levels, key=lambda level: level.price)
            for level in levels:
                if remaining <= 1e-12:
                    break
                take = min(remaining, level.size)
                price = self._adjusted_price(level.price)
                notional += take * price
                fees += self._taker_fee(take, price)
                remaining -= take
            if remaining > 1e-9:
                raise QuoteError(f"{self.id} has insufficient ask depth for {shares:.6f} shares")
        elif self.ask_price is not None:
            price = self._adjusted_price(self.ask_price)
            notional = shares * price
            fees = self._taker_fee(shares, price)
        else:
            raise QuoteError(f"{self.id} has neither ask_levels nor ask_price")

        subtotal_base = (notional + fees) * self.cost_fx_to_base
        cost = subtotal_base * (1.0 + self.extra_cost_rate) + self.flat_fee_base
        payout_if_win = shares * self.payout_fx_to_base
        notes = []
        if shares > requested_shares:
            notes.append("raised_to_min_shares")
        if self.slippage_bps:
            notes.append("slippage_applied")
        if self.fee_rate:
            notes.append("taker_fee_applied")

        return Quote(
            instrument_id=self.id,
            source=self.source,
            outcome=self.outcome,
            label=self.label or self.id,
            target_payout=target_payout,
            payout_if_win=payout_if_win,
            cost=cost,
            stake_or_shares=shares,
            unit="shares",
            average_price=notional / shares if shares else None,
            notes=tuple(notes),
        )

    def _adjusted_price(self, price: float) -> float:
        adjusted = price * (1.0 + self.slippage_bps / 10_000.0)
        return min(adjusted, 1.0)

    def _taker_fee(self, shares: float, price: float) -> float:
        return shares * self.fee_rate * price * (1.0 - price)


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    outcomes: tuple[Outcome, ...]
    instruments: tuple[Instrument, ...]
    base_currency: str = "CNY"

    @property
    def outcome_ids(self) -> set[str]:
        return {outcome.id for outcome in self.outcomes}


@dataclass(frozen=True)
class ArbitragePortfolio:
    event_id: str
    title: str
    base_currency: str
    target_payout: float
    quotes: tuple[Quote, ...]
    missing_outcomes: tuple[str, ...] = ()

    @property
    def total_cost(self) -> float:
        return sum(quote.cost for quote in self.quotes)

    @property
    def minimum_payout(self) -> float:
        if self.missing_outcomes:
            return 0.0
        return min((quote.payout_if_win for quote in self.quotes), default=0.0)

    @property
    def guaranteed_profit(self) -> float:
        return self.minimum_payout - self.total_cost

    @property
    def cost_ratio(self) -> float:
        if self.minimum_payout <= 0:
            return float("inf")
        return self.total_cost / self.minimum_payout

    @property
    def profit_roi(self) -> float:
        if self.total_cost <= 0:
            return 0.0
        return self.guaranteed_profit / self.total_cost

    @property
    def is_complete(self) -> bool:
        return not self.missing_outcomes

    @property
    def is_arbitrage(self) -> bool:
        return self.is_complete and self.guaranteed_profit > 0


def analyze_event(event: Event, target_payout: float) -> ArbitragePortfolio:
    target_payout = _positive_float(target_payout, "target_payout")
    quotes: list[Quote] = []
    missing: list[str] = []

    for outcome in event.outcomes:
        candidates: list[Quote] = []
        for instrument in event.instruments:
            if instrument.outcome != outcome.id:
                continue
            try:
                candidates.append(instrument.quote_for_payout(target_payout))
            except QuoteError:
                continue
        if not candidates:
            missing.append(outcome.id)
            continue
        quotes.append(min(candidates, key=lambda quote: quote.cost))

    return ArbitragePortfolio(
        event_id=event.id,
        title=event.title,
        base_currency=event.base_currency,
        target_payout=target_payout,
        quotes=tuple(quotes),
        missing_outcomes=tuple(missing),
    )


def optimize_for_budget(event: Event, budget: float, *, iterations: int = 64, scan_points: int = 256) -> ArbitragePortfolio:
    budget = _positive_float(budget, "budget")
    scan_points = max(16, scan_points)
    upper = _find_scan_upper(event, budget)
    samples: list[tuple[float, ArbitragePortfolio]] = []
    best: tuple[float, ArbitragePortfolio] | None = None

    for index in range(1, scan_points + 1):
        target = upper * index / scan_points
        portfolio = analyze_event(event, target)
        samples.append((target, portfolio))
        if _is_profitable_under_budget(portfolio, budget):
            best = (target, portfolio)

    if best is None:
        return analyze_event(event, budget)

    best_index = max(index for index, (_, portfolio) in enumerate(samples) if portfolio is best[1])
    low = samples[best_index][0]
    high = upper
    for target, portfolio in samples[best_index + 1 :]:
        high = target
        if not _is_profitable_under_budget(portfolio, budget):
            break

    best_portfolio = best[1]
    for _ in range(iterations):
        mid = (low + high) / 2.0
        portfolio = analyze_event(event, mid)
        if _is_profitable_under_budget(portfolio, budget):
            best_portfolio = portfolio
            low = mid
        else:
            high = mid
    return best_portfolio


def parse_instrument(raw: Mapping[str, Any]) -> Instrument:
    instrument_type = raw.get("type")
    if instrument_type == "fixed_odds":
        return FixedOddsInstrument.from_mapping(raw)
    if instrument_type == "prediction_share":
        return PredictionShareInstrument.from_mapping(raw)
    raise ValueError(f"Unsupported instrument type: {instrument_type!r}")


def event_from_mapping(raw: Mapping[str, Any], *, default_base_currency: str = "CNY") -> Event:
    outcomes = tuple(Outcome(id=str(item["id"]), label=item.get("label")) for item in raw["outcomes"])
    instruments = tuple(parse_instrument(item) for item in raw.get("instruments", []))
    event = Event(
        id=str(raw["id"]),
        title=str(raw.get("title", raw["id"])),
        outcomes=outcomes,
        instruments=instruments,
        base_currency=str(raw.get("base_currency", default_base_currency)),
    )
    _validate_event(event)
    return event


def events_from_payload(payload: Mapping[str, Any]) -> tuple[Event, ...]:
    default_base_currency = str(payload.get("base_currency", "CNY"))
    if "events" in payload:
        return tuple(event_from_mapping(item, default_base_currency=default_base_currency) for item in payload["events"])
    return (event_from_mapping(payload, default_base_currency=default_base_currency),)


def _validate_event(event: Event) -> None:
    if not event.outcomes:
        raise ValueError(f"{event.id} has no outcomes")
    unknown = sorted({instrument.outcome for instrument in event.instruments} - event.outcome_ids)
    if unknown:
        raise ValueError(f"{event.id} has instruments for unknown outcomes: {', '.join(unknown)}")


def _is_profitable_under_budget(portfolio: ArbitragePortfolio, budget: float) -> bool:
    return portfolio.is_arbitrage and portfolio.total_cost <= budget + 1e-9


def _find_scan_upper(event: Event, budget: float) -> float:
    upper = budget
    seen_profitable = False
    for _ in range(32):
        portfolio = analyze_event(event, upper)
        if _is_profitable_under_budget(portfolio, budget):
            seen_profitable = True
            upper *= 2.0
            continue
        if portfolio.total_cost > budget or seen_profitable or upper > budget:
            return upper
        upper *= 2.0
    return upper


def _append_once(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def _positive_float(value: Any, name: str) -> float:
    parsed = float(value)
    if not isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return parsed


def _non_negative_float(value: Any, name: str) -> float:
    parsed = float(value)
    if not isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return parsed


def _optional_positive_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return _positive_float(value, name)


def _rate(value: Any, name: str) -> float:
    parsed = _non_negative_float(value, name)
    if parsed >= 1:
        raise ValueError(f"{name} must be less than 1")
    return parsed


def _probability(value: Any, name: str) -> float:
    parsed = _positive_float(value, name)
    if parsed > 1:
        raise ValueError(f"{name} must be <= 1")
    return parsed
