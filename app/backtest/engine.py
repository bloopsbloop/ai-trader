from app.backtest.report import Report
from app.backtest.statistics import EquityPoint, Statistics
from app.core.broker import Broker
from app.core.portfolio import Portfolio
from app.core.risk_manager import RiskManager
from app.models.order import OrderStatus
from app.models.signal import Signal
from app.models.trade import Trade
from app.strategies.base import MarketContext


class BacktestEngine:
    def __init__(
        self,
        strategy,
        start_balance=1000.0,
        symbol="BTCUSDT",
        maker_commission=0.0002,
        taker_commission=0.0004,
        slippage=0.0005,
        stop_loss=None,
        take_profit=None,
        trailing_stop=None,
        position_size=0.02,
        max_position_fraction=0.20,
        periods_per_year=365,
        indicator_lookback=None,
    ):
        if indicator_lookback is not None and indicator_lookback < 1:
            raise ValueError("indicator_lookback must be positive")
        for name, value in (("maker_commission", maker_commission), ("taker_commission", taker_commission), ("slippage", slippage)):
            self._validate_rate(name, value)
        for name, value in (("stop_loss", stop_loss), ("take_profit", take_profit), ("trailing_stop", trailing_stop)):
            if value is not None:
                self._validate_rate(name, value)
                if value == 0:
                    raise ValueError(f"{name} must be greater than zero")
        self.strategy = strategy
        self.symbol = symbol
        self.start_balance = start_balance
        self.slippage = slippage
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.trailing_stop = trailing_stop
        self.periods_per_year = periods_per_year
        self.indicator_lookback = indicator_lookback
        self.risk_manager = RiskManager(risk_percent=position_size, max_position_fraction=max_position_fraction)
        self.broker = Broker(taker_commission, self.risk_manager, maker_commission)
        self._reset_run_state()

    def run(self, candles, print_report=True, warmup_candles=()):
        self._reset_run_state()
        observed = list(warmup_candles)
        candle_list = list(candles)
        for candle in candle_list:
            observed.append(candle)
            visible = observed[-self.indicator_lookback:] if self.indicator_lookback is not None else observed
            context = MarketContext(tuple(visible))
            if self.portfolio.has_position():
                self._process_protective_exit(candle)
                if not self.portfolio.has_position():
                    self.equity_curve.append(EquityPoint(candle.open_time, self.portfolio.equity(candle.close)))
                    continue
            signal = self.strategy.analyze_context(context) if hasattr(self.strategy, "analyze_context") else self.strategy.analyze(context.prices)
            if not self.portfolio.has_position() and signal == Signal.BUY:
                self._open_position(candle, context)
            elif self.portfolio.has_position() and signal == Signal.SELL:
                self._close_position(candle.close, candle.open_time, "signal")
            self.equity_curve.append(EquityPoint(candle.open_time, self.portfolio.equity(candle.close)))
        if candle_list and self.portfolio.has_position():
            last_candle = candle_list[-1]
            self._close_position(last_candle.close, last_candle.open_time, "end_of_test")
            self.equity_curve[-1] = EquityPoint(last_candle.open_time, self.portfolio.equity(last_candle.close))
        self.statistics = Statistics(self.trades, self.start_balance, self.portfolio.balance, self.equity_curve, self.periods_per_year)
        if print_report:
            Report.print(self.statistics)
        return self.statistics

    def print_statistics(self):
        if self.statistics is None:
            raise RuntimeError("Run the backtest before printing statistics")
        return Report.print(self.statistics)

    def _open_position(self, candle, context):
        amount = self.risk_manager.calculate_position_size(self.portfolio.balance)
        order = self.broker.buy(self.portfolio, candle.close * (1 + self.slippage), amount, self.symbol)
        if order.status == OrderStatus.FILLED:
            self._entry_order = order
            self._entry_time = candle.open_time
            self._highest_price = candle.high
            self._stop_price, self._take_price = self.strategy.protective_levels(context, order.price) if hasattr(self.strategy, "protective_levels") else (None, None)

    def _process_protective_exit(self, candle):
        entry_price = self._entry_order.price
        stop_price = self._stop_price if self._stop_price is not None else entry_price * (1 - self.stop_loss) if self.stop_loss is not None else None
        take_price = self._take_price if self._take_price is not None else entry_price * (1 + self.take_profit) if self.take_profit is not None else None
        if stop_price is not None and candle.low <= stop_price:
            self._close_position(min(candle.open, stop_price), candle.open_time, "stop_loss")
        elif take_price is not None and candle.high >= take_price:
            self._close_position(take_price, candle.open_time, "take_profit")
        elif self.trailing_stop is not None and candle.low <= self._highest_price * (1 - self.trailing_stop):
            self._close_position(min(candle.open, self._highest_price * (1 - self.trailing_stop)), candle.open_time, "trailing_stop")
        if self.portfolio.has_position():
            self._highest_price = max(self._highest_price, candle.high)

    def _close_position(self, price, timestamp, reason):
        entry_order = self._entry_order
        order = self.broker.sell(self.portfolio, price * (1 - self.slippage))
        if order.status == OrderStatus.FILLED:
            self.trades.append(Trade(
                entry=entry_order.price, exit=order.price, profit=order.realized_pnl,
                profit_percent=(order.price - entry_order.price) / entry_order.price * 100,
                quantity=order.quantity, commission=entry_order.commission + order.commission,
                exit_reason=reason, entry_time=self._entry_time, exit_time=timestamp,
            ))
            self._entry_order = None
            self._entry_time = None
            self._highest_price = None
            self._stop_price = None
            self._take_price = None

    def _reset_run_state(self):
        self.portfolio = Portfolio(self.start_balance)
        if hasattr(self, "broker"):
            self.broker.orders = []
        self.trades = []
        self.equity_curve = []
        self.statistics = None
        self._entry_order = None
        self._entry_time = None
        self._highest_price = None
        self._stop_price = None
        self._take_price = None

    @staticmethod
    def _validate_rate(name, value):
        if not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
