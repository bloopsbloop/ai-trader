# AI Trader

Python-проект для исследования торговых стратегий и их бэктестинга на данных Binance.

## Professional Backtest Engine

`BacktestEngine` рассчитывает equity curve, максимальную просадку, profit factor,
средние прибыль и убыток, expectancy и Sharpe ratio. Он применяет отдельные
maker/taker-комиссии, слиппедж, size позиции, stop loss, take profit и trailing stop.

```python
from app.backtest.engine import BacktestEngine

engine = BacktestEngine(
    strategy=my_strategy,
    start_balance=1000,
    symbol="BTCUSDT",
    maker_commission=0.0002,
    taker_commission=0.0004,
    slippage=0.0005,
    stop_loss=0.02,
    take_profit=0.04,
    trailing_stop=0.015,
    position_size=0.02,
)
statistics = engine.run(candles)
print(statistics.max_drawdown)
```

`run()` выводит форматированный отчёт и возвращает объект `Statistics`; equity curve
доступна в `engine.equity_curve`.

## Historical Data Engine

`HistoricalDataProvider` загружает исторические свечи Binance и кэширует их в
`data/history/<SYMBOL>_<INTERVAL>.csv`. Если файл уже есть, данные берутся из
него без нового запроса к Binance.

Поддерживаемые интервалы: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`. Символ передаётся
как строка Binance, например `BTCUSDT`, `ETHUSDT` или любой другой доступный на
спотовом рынке Binance.

```python
from app.core.historical_data import HistoricalDataProvider

provider = HistoricalDataProvider()
candles = provider.get_history(
    symbol="BTCUSDT",
    interval="1h",
    start_date="2024-01-01",
    end_date="2024-01-31",
)

print(f"Loaded {len(candles)} candles")
```

## Tests

```powershell
python -m pytest
```
