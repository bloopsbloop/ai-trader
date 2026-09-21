from math import sqrt
from statistics import stdev
from datetime import datetime, timezone

from app.paper.runner import PaperRunner
from app.paper.storage import PaperStorage


def build_report(experiment_id, storage=None):
    storage = storage or PaperStorage()
    experiment, state = storage.get_experiment(experiment_id)
    if experiment is None:
        raise ValueError("Unknown experiment")
    portfolio = PaperRunner._portfolio(experiment, state)
    equity = portfolio.equity(state["last_price"] or portfolio.position.entry if portfolio.position else portfolio.balance)
    unrealized = (portfolio.position.quantity * state["last_price"] - portfolio.position.cost_basis
                  if portfolio.position else 0.0)
    trades = storage.trades(experiment_id)
    closed = [trade for trade in trades if trade["side"] == "SELL"]
    profits = [trade["realized_pnl"] for trade in closed]
    wins = sum(profit > 0 for profit in profits)
    losses = sum(profit < 0 for profit in profits)
    gross_profit = sum(profit for profit in profits if profit > 0)
    gross_loss = abs(sum(profit for profit in profits if profit < 0))
    curve = [point["equity"] for point in state["equity_curve"]]
    peak = curve[0] if curve else experiment.starting_balance
    drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak * 100 if peak else 0)
    returns = [current / previous - 1 for previous, current in zip(curve, curve[1:]) if previous]
    sharpe = sum(returns) / len(returns) / stdev(returns) * sqrt(365 * (24 if experiment.timeframe == "1h" else 6)) if len(returns) > 1 and stdev(returns) else 0.0
    return {"experiment": experiment, "state": state, "equity": equity, "unrealized_pnl": unrealized,
            "trades": len(closed), "wins": wins, "losses": losses,
            "win_rate": wins / len(closed) * 100 if closed else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
            "max_drawdown": drawdown, "sharpe": sharpe,
            "expectancy": sum(profits) / len(profits) if profits else 0.0,
            "benchmark_equity": state["benchmark_equity"]}


def format_report(experiment_id, storage=None):
    report = build_report(experiment_id, storage)
    experiment, state = report["experiment"], report["state"]
    runtime = datetime.now(timezone.utc) - datetime.fromisoformat(experiment.created_at)
    return "\n".join(["=" * 46, "PAPER TRADING REPORT", "=" * 46,
                      f"Experiment: {experiment.experiment_id}", f"Start date: {experiment.created_at}",
                      f"Runtime: {runtime}", f"Status: {experiment.status.value}",
                      f"Strategy: {experiment.strategy}", f"Parameters: {experiment.strategy_parameters}",
                      f"Starting balance: {experiment.starting_balance:.2f}", f"Cash: {state['cash']:.2f}",
                      f"Equity: {report['equity']:.2f}", f"Realized PnL: {state['realized_pnl']:.2f}",
                      f"Unrealized PnL: {report['unrealized_pnl']:.2f}", f"Fees: {state['fees']:.2f}",
                      f"Trades: {report['trades']} | Wins: {report['wins']} | Losses: {report['losses']}",
                      f"Win rate: {report['win_rate']:.2f}% | Profit factor: {report['profit_factor']}",
                      f"Max drawdown: {report['max_drawdown']:.2f}% | Sharpe: {report['sharpe']:.2f}",
                      f"Expectancy: {report['expectancy']:.2f}", f"Buy & Hold equity: {report['benchmark_equity']:.2f}", "=" * 46])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id")
    args = parser.parse_args()
    print(format_report(args.experiment_id))
