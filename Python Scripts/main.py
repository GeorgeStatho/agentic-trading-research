import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from algMl import Prediction, train_and_predict
from Trading import StockTrades, trading_client
from newsCollecting import getLatestTop100, jsonToPy

REFRESH_INTERVAL_SECONDS = 30 * 60  # half-hour
MARKET_RECHECK_SECONDS = 5 * 60
MIN_CONFIDENCE = 0.10
UPSIDE_THRESHOLD = 1.01  # buy if predicted price 1% higher than current
DOWNSIDE_THRESHOLD = 0.99  # sell if predicted price 1% lower than current
DEFAULT_POSITION_SIZE = 1


@dataclass
class TradeRecord:
    symbol: str
    action: str
    qty: int
    expected_profit: float
    confidence: float
    predicted_price: float
    reference_price: float


def market_is_open() -> bool:
    """
    Queries Alpaca clock to check if US equities market is open.
    """
    try:
        clock = trading_client.get_clock()
        return bool(clock.is_open)
    except Exception as exc:
        logging.exception("Failed to check market clock: %s", exc)
        # fail safe: pause until next check
        return False


def refresh_news_and_market(symbols: Dict[str, str]) -> None:
    """
    Pull fresh news and price quotes.
    """
    logging.info("Fetching latest news articles...")
    getLatestTop100(symbols)
    logging.info("Assuming MarketData.py streamer is running separately for quote updates.")


def execute_paper_trade(symbol: str, action: str, qty: int) -> None:
    """
    Dispatches a market order to the Alpaca paper trading endpoint via Trading.StockTrades.
    """
    trader = StockTrades(symbol, qty, "Day", limit=False)
    if action == "BUY":
        trader.ImmediateStockBuy(qty)
    else:
        trader.ImmediateStockSell(qty)


def plan_trades(predictions: List[Prediction]) -> List[TradeRecord]:
    """
    Basic rule-based strategy using model predictions.
    """
    trades: List[TradeRecord] = []
    for pred in predictions:
        if pred.actual_price <= 0:
            continue
        if pred.confidence < MIN_CONFIDENCE:
            continue

        ratio = pred.predicted_price / pred.actual_price if pred.actual_price else 1.0
        action = None
        expected_profit = 0.0

        if ratio >= UPSIDE_THRESHOLD:
            action = "BUY"
            expected_profit = (pred.predicted_price - pred.actual_price) * DEFAULT_POSITION_SIZE
        elif ratio <= DOWNSIDE_THRESHOLD:
            action = "SELL"
            expected_profit = (pred.actual_price - pred.predicted_price) * DEFAULT_POSITION_SIZE

        if action:
            trades.append(
                TradeRecord(
                    symbol=pred.symbol,
                    action=action,
                    qty=DEFAULT_POSITION_SIZE,
                    expected_profit=expected_profit,
                    confidence=pred.confidence,
                    predicted_price=pred.predicted_price,
                    reference_price=pred.actual_price,
                )
            )
    return trades


def _prediction_map(predictions: List[Prediction]) -> Dict[str, Prediction]:
    return {pred.symbol: pred for pred in predictions}


def _select_position_to_sell(
    positions,
    prediction_lookup: Dict[str, Prediction],
    min_expected_gain: float,
    excluded_symbols: set[str],
):
    worst_candidate = None
    worst_score = float("inf")
    for pos in positions:
        symbol = getattr(pos, "symbol", None)
        if not symbol or symbol in excluded_symbols:
            continue
        prediction = prediction_lookup.get(symbol)
        if not prediction:
            continue
        score = prediction.predicted_price - prediction.actual_price
        if score < min_expected_gain and score < worst_score:
            worst_score = score
            worst_candidate = pos
    return worst_candidate


def ensure_buying_power(predictions: List[Prediction], trades: List[TradeRecord]) -> List[TradeRecord]:
    if not trades:
        return []

    prediction_lookup = _prediction_map(predictions)
    account = trading_client.get_account()
    available_cash = float(account.buying_power)

    sells = [t for t in trades if t.action == "SELL"]
    buys = sorted((t for t in trades if t.action == "BUY"), key=lambda t: t.expected_profit, reverse=True)

    # assume scheduled sells will complete first and free capital
    for sell in sells:
        available_cash += sell.reference_price * sell.qty

    finalized: List[TradeRecord] = []
    finalized.extend(sells)

    if buys:
        positions = trading_client.get_all_positions()
    else:
        positions = []

    already_scheduled_for_sale = {t.symbol for t in sells}

    for buy in buys:
        cost = buy.reference_price * buy.qty
        if cost <= available_cash:
            finalized.append(buy)
            available_cash -= cost
            continue

        candidate = _select_position_to_sell(
            positions,
            prediction_lookup,
            buy.expected_profit,
            already_scheduled_for_sale,
        )
        if not candidate:
            logging.info(
                "Skipping BUY %s due to insufficient buying power and no lower-scoring positions to rotate.",
                buy.symbol,
            )
            continue

        sell_qty = int(float(candidate.qty))
        if sell_qty <= 0:
            continue

        current_price = float(candidate.current_price)
        market_value = float(candidate.market_value)
        prediction = prediction_lookup.get(candidate.symbol)
        sell_trade = TradeRecord(
            symbol=candidate.symbol,
            action="SELL",
            qty=sell_qty,
            expected_profit=0.0,
            confidence=prediction.confidence if prediction else 0.0,
            predicted_price=prediction.predicted_price if prediction else current_price,
            reference_price=current_price,
        )
        logging.info(
            "Rotating out of %s to free $%.2f for %s.",
            sell_trade.symbol,
            market_value,
            buy.symbol,
        )
        finalized.append(sell_trade)
        already_scheduled_for_sale.add(sell_trade.symbol)
        available_cash += market_value

        if cost <= available_cash:
            finalized.append(buy)
            available_cash -= cost
        else:
            logging.info(
                "Still insufficient buying power for %s after rotation; skipping.",
                buy.symbol,
            )

    return finalized


def run_cycle() -> None:
    symbols = jsonToPy()
    refresh_news_and_market(symbols)

    logging.info("Training deep learning model on latest data...")
    predictions = train_and_predict(print_summary=False)
    logging.info("Model produced %d predictions.", len(predictions))

    trades = plan_trades(predictions)
    if not trades:
        logging.info("No trades met confidence/threshold requirements this cycle.")
        return

    trades = ensure_buying_power(predictions, trades)
    if not trades:
        logging.info("After applying buying power constraints, no trades will be executed this cycle.")
        return

    for trade in trades:
        if trade.action == "BUY":
            account = trading_client.get_account()
            estimated_cost = trade.reference_price * trade.qty
            available = float(account.buying_power)
            if estimated_cost > available:
                logging.info(
                    "Skipping BUY %s due to insufficient live buying power (need %.2f, have %.2f).",
                    trade.symbol,
                    estimated_cost,
                    available,
                )
                continue

        logging.info(
            "Executing %s for %s (qty=%s, predicted=%.2f, current=%.2f, confidence=%.2f, expected profit=%.2f)",
            trade.action,
            trade.symbol,
            trade.qty,
            trade.predicted_price,
            trade.reference_price,
            trade.confidence,
            trade.expected_profit,
        )
        try:
            execute_paper_trade(trade.symbol, trade.action, trade.qty)
        except Exception as exc:
            logging.exception("Paper trade for %s failed: %s", trade.symbol, exc)


def main_loop():
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    log_file_path = logs_dir / "bot.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file_path, encoding="utf-8"),
        ],
    )

    logging.info("Starting paper-trading training loop.")
    while True:
        cycle_start = datetime.now()

        if not market_is_open():
            logging.info("Market is closed. Pausing training/trading loop.")
            time.sleep(MARKET_RECHECK_SECONDS)
            continue
        try:
            run_cycle()
        except Exception as exc:
            logging.exception("Cycle failed: %s", exc)

        elapsed = (datetime.now() - cycle_start).total_seconds()
        sleep_time = max(0, REFRESH_INTERVAL_SECONDS - elapsed)
        logging.info("Sleeping %.1f seconds until next cycle.", sleep_time)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main_loop()
