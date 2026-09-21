from datetime import datetime, timedelta
import pytest

from app.models.candle import Candle
from app.research.evaluation import buy_and_hold, diagnostic_flags


def test_buy_and_hold_uses_only_given_period():
    candles = [Candle(datetime(2024, 1, 1) + timedelta(hours=index), price, price, price, price, 1)
               for index, price in enumerate((100, 120, 90, 110))]
    result = buy_and_hold(candles, 365 * 24)
    assert result["return"] == pytest.approx(10.0)
    assert result["max_drawdown"] == 25.0


def test_flags_are_diagnostics_not_score_changes():
    from app.optimizer.metrics import BacktestMetrics

    train = BacktestMetrics(10, 100, 2, 1, 2, 50, 20, 5)
    validation = BacktestMetrics(-1, -10, 4, -1, 0.5, 30, 10, -1)
    test = BacktestMetrics(-2, -20, 25, 5, 0.3, 20, 2, -3)
    flags = diagnostic_flags(train, validation, test, 0.2)
    assert "train_good_validation_bad" in flags
    assert "too_few_trades" in flags
    assert "unstable_parameters" in flags
    assert "excessive_drawdown" in flags
    assert "suspiciously_high_sharpe" in flags
