from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .io import dump_json, load_events, write_json
from .model import ArbitragePortfolio, Event, QuoteError, analyze_event, optimize_for_budget
from .polymarket import PolymarketClient, PolymarketFetchError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worldcup-arb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="analyze one or more events at a fixed payout target")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("--target-payout", type=float, required=True)

    table_parser = subparsers.add_parser("table", help="show per-outcome source costs at a fixed payout target")
    table_parser.add_argument("input", type=Path)
    table_parser.add_argument("--target-payout", type=float, default=100.0)

    optimize_parser = subparsers.add_parser("optimize", help="find the largest profitable hedged payout under a budget")
    optimize_parser.add_argument("input", type=Path)
    optimize_parser.add_argument("--budget", type=float, required=True)

    fetch_parser = subparsers.add_parser("fetch-polymarket", help="fetch a Polymarket event snapshot by slug")
    fetch_parser.add_argument("slug")
    fetch_parser.add_argument("--out", type=Path)
    fetch_parser.add_argument("--no-books", action="store_true", help="skip CLOB order books")
    fetch_parser.add_argument("--geoblock", action="store_true", help="also print geoblock status")

    args = parser.parse_args(argv)

    if args.command == "analyze":
        events = load_events(args.input)
        for portfolio in (analyze_event(event, args.target_payout) for event in events):
            print_portfolio(portfolio)
        return 0

    if args.command == "optimize":
        events = load_events(args.input)
        for portfolio in (optimize_for_budget(event, args.budget) for event in events):
            print_portfolio(portfolio)
        return 0

    if args.command == "table":
        events = load_events(args.input)
        for event in events:
            print_quote_table(event, args.target_payout)
        return 0

    if args.command == "fetch-polymarket":
        client = PolymarketClient()
        try:
            snapshot = client.snapshot_event(args.slug, include_books=not args.no_books)
            if args.out:
                dump_json(args.out, snapshot)
                print(f"Wrote {args.out}")
            else:
                write_json(sys.stdout, snapshot)
            if args.geoblock:
                print(client.get_geoblock())
        except PolymarketFetchError as exc:
            print(f"Polymarket fetch failed: {exc}", file=sys.stderr)
            return 2
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def print_portfolio(portfolio: ArbitragePortfolio) -> None:
    currency = portfolio.base_currency
    print(f"\n{portfolio.title} ({portfolio.event_id})")
    if portfolio.missing_outcomes:
        print(f"  缺少结果报价: {', '.join(portfolio.missing_outcomes)}")
    print(f"  target_payout: {portfolio.target_payout:.2f} {currency}")
    print(f"  total_cost: {portfolio.total_cost:.2f} {currency}")
    print(f"  minimum_payout: {portfolio.minimum_payout:.2f} {currency}")
    print(f"  guaranteed_profit: {portfolio.guaranteed_profit:.2f} {currency}")
    print(f"  cost_ratio: {portfolio.cost_ratio:.6f}")
    print(f"  profit_roi: {portfolio.profit_roi:.2%}")
    print(f"  arbitrage: {'YES' if portfolio.is_arbitrage else 'NO'}")
    print("  legs:")
    for quote in portfolio.quotes:
        avg = "" if quote.average_price is None else f", avg_price={quote.average_price:.4f}"
        notes = "" if not quote.notes else f", notes={'+'.join(quote.notes)}"
        print(
            "   - "
            f"{quote.outcome}: {quote.source} {quote.label}, "
            f"{quote.stake_or_shares:.6f} {quote.unit}, "
            f"cost={quote.cost:.2f}, payout={quote.payout_if_win:.2f}{avg}{notes}"
        )


def print_quote_table(event: Event, target_payout: float) -> None:
    print(f"\n{event.title} ({event.id})")
    print(f"  target_payout: {target_payout:.2f} {event.base_currency}")
    print("  outcome | source | label | cost | cost_ratio")
    for outcome in event.outcomes:
        rows = []
        for instrument in event.instruments:
            if instrument.outcome != outcome.id:
                continue
            try:
                quote = instrument.quote_for_payout(target_payout)
            except QuoteError as exc:
                rows.append((instrument.source, instrument.label or instrument.id, "ERR", str(exc)))
                continue
            rows.append((quote.source, quote.label, f"{quote.cost:.2f}", f"{quote.cost_per_payout:.6f}"))
        if not rows:
            print(f"  {outcome.id} | - | missing | - | -")
        for source, label, cost, ratio in rows:
            print(f"  {outcome.id} | {source} | {label} | {cost} | {ratio}")


if __name__ == "__main__":
    raise SystemExit(main())
