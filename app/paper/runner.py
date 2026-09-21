from datetime import datetime

from app.core.portfolio import Portfolio
from app.models.position import Position
from app.models.signal import Signal
from app.models.trade import Trade
from app.paper.broker import PaperBroker
from app.paper.market_data import PublicMarketData
from app.paper.storage import PaperStorage
from app.strategies.base import MarketContext
from app.strategies.ema_strategy import EMAStrategy
from app.strategies.rsi_strategy import RSIStrategy
from app.strategies.ema_rsi_strategy import EMARSIStrategy
from app.strategies.ema_rsi_atr_strategy import EMARSIATRStrategy


STRATEGIES = {strategy.__name__: strategy for strategy in (EMAStrategy, RSIStrategy, EMARSIStrategy, EMARSIATRStrategy)}


class PaperRunner:
    def __init__(self, storage=None, market_data=None):
        self.storage = storage or PaperStorage()
        self.market_data = market_data or PublicMarketData()

    def run_once(self):
        messages = []
        for experiment, state in self.storage.active_experiments():
            messages.extend(self.process(experiment, state))
        return messages

    def process(self, experiment, state):
        candles = self.market_data.closed_candles(experiment.symbol, experiment.timeframe)
        last = state["last_processed_candle"]
        if last is None and candles:
            latest = candles[-1]
            state["last_processed_candle"] = latest.open_time.isoformat()
            state["benchmark_entry"] = latest.close
            state["last_price"] = latest.close
            self.storage.save_state(experiment.experiment_id, state)
            return [f"{latest.open_time} {experiment.experiment_id[:8]} initialized; next closed candle starts forward evaluation"]
        new_candles = [candle for candle in candles if last is None or candle.open_time.isoformat() > last]
        messages = []
        for candle in new_candles:
            messages.append(self._process_candle(experiment, state, candles, candle))
            state["last_processed_candle"] = candle.open_time.isoformat()
        if new_candles:
            self.storage.save_state(experiment.experiment_id, state)
        return messages

    def _process_candle(self, experiment, state, candles, candle):
        portfolio = self._portfolio(experiment, state)
        broker = PaperBroker(experiment.maker_commission, experiment.taker_commission, experiment.slippage,
                             experiment.position_size, experiment.max_position_fraction)
        strategy = STRATEGIES[experiment.strategy](**experiment.strategy_parameters)
        context = MarketContext(tuple(c for c in candles if c.open_time <= candle.open_time))
        action = "HOLD"
        if portfolio.has_position():
            signal = strategy.analyze_context(context)
            if signal == Signal.SELL:
                order = broker.market_sell(portfolio, candle.close)
                self.storage.add_trade(experiment.experiment_id, order, candle.open_time.isoformat())
                action = "SELL"
        else:
            signal = strategy.analyze_context(context)
            if signal == Signal.BUY:
                order = broker.market_buy(portfolio, candle.close, experiment.symbol)
                self.storage.add_trade(experiment.experiment_id, order, candle.open_time.isoformat())
                action = "BUY"
        equity = portfolio.equity(candle.close)
        state["last_price"] = candle.close
        if state["benchmark_entry"] is None:
            state["benchmark_entry"] = candle.close
        state["benchmark_equity"] = experiment.starting_balance * candle.close / state["benchmark_entry"]
        state["equity_peak"] = max(state["equity_peak"], equity)
        state["equity_curve"].append({"time": candle.open_time.isoformat(), "equity": equity})
        self._write_portfolio(state, portfolio)
        position = portfolio.position.quantity if portfolio.position else 0.0
        return f"{candle.open_time} {experiment.experiment_id[:8]} {experiment.symbol} {experiment.timeframe} close={candle.close:.2f} signal={signal.value} action={action} position={position:.8f} cash={portfolio.balance:.2f} equity={equity:.2f} realized={portfolio.realized_pnl:.2f}"

    @staticmethod
    def _portfolio(experiment, state):
        portfolio = Portfolio(experiment.starting_balance)
        portfolio.balance = state["cash"]
        portfolio.realized_pnl = state["realized_pnl"]
        portfolio.total_commissions = state["fees"]
        if state["position"]:
            portfolio.position = Position(**state["position"])
        return portfolio

    @staticmethod
    def _write_portfolio(state, portfolio):
        state["cash"] = portfolio.balance
        state["realized_pnl"] = portfolio.realized_pnl
        state["fees"] = portfolio.total_commissions
        state["position"] = portfolio.position.__dict__ if portfolio.position else None
