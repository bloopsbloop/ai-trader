# AI Trader

## Milestone 11.4 — forward ML experiment

Shadow observations now belong to the immutable experiment
`ml_v1_market_forward_001`. Resolved predictions produce same-sample ML versus
heuristic comparisons, calibration buckets, per-symbol metrics, data-quality
warnings, and theoretical fixed-exposure equity curves.

```powershell
python -m app.ml.shadow status
python -m app.ml.shadow report
python -m app.ml.shadow resolve
python -m app.ml.shadow export
```

Exports are written to `data/ml/forward/`. All results remain analytical and do
not call PaperBroker. See
[`docs/MILESTONE_11_4_FORWARD.md`](docs/MILESTONE_11_4_FORWARD.md).

## Milestone 11.3 — ML shadow mode

`ML_V1_MARKET` now observes Batch 3 closed candles and records forward predictions
without influencing ranking, BUY/WATCH/PASS, RiskManager, or PaperBroker. Inspect
the local shadow state with:

```powershell
python -m app.ml.shadow status
python -m app.ml.shadow report
```

Predictions are unique per model, symbol, timeframe, and candle close. Four-hour
outcomes are resolved only after the required future candle closes. The dashboard
page **ML Shadow** shows predictions and only exposes performance metrics after at
least 20 resolved samples. See
[`docs/MILESTONE_11_3_SHADOW.md`](docs/MILESTONE_11_3_SHADOW.md).

## Milestone 11.2 — ML_V1_MARKET

Train the market-only HistGradientBoosting model and evaluate it with the fixed
chronological dataset contract:

```powershell
python -m app.ml.train --force
python -m app.ml.evaluate
```

The trainer uses train and validation only for model, class-weight, calibration,
and threshold selection. Evaluation opens the test partition afterward and exports
classification, trading, baseline, three-fold walk-forward, and permutation
importance reports. The registered model remains an offline research artifact; it
is not connected to the Batch 3 runner. See
[`docs/MILESTONE_11_2_ML_V1.md`](docs/MILESTONE_11_2_ML_V1.md).

## Milestone 11.1 — ML dataset pipeline

Build chronological WITH_NEWS and WITHOUT_NEWS datasets with:

```powershell
python -m app.ml.dataset
```

The pipeline uses locally cached closed candles, backward multi-timeframe joins,
cost-adjusted future-return labels, purged chronological splits and walk-forward
folds. See [`docs/MILESTONE_11_1_DATASET.md`](docs/MILESTONE_11_1_DATASET.md).

## Milestone 10 — isolated AI Scanner / Batch 3

Local validation and standalone runner/dashboard commands:
[`docs/BATCH3_LOCAL.md`](docs/BATCH3_LOCAL.md). Displayed portfolios are
`with_news` / `without_news`; existing database IDs remain unchanged.

See `docs/MILESTONE_10.md` for architecture, migration boundaries and limitations.

```powershell
python -m app.ai.run                  # scan only, no virtual fills
python -m app.ai.run --paper           # one forward PAPER cycle
python -m app.ai.run --paper --loop    # separate Batch 3 process
```

The separate database is `data/ai/batch3.sqlite3`; `--db` can override it but rejects
existing non-AI databases. RSS sources can be supplied with repeated `--feed HTTPS_URL`.
Telegram delivery is disabled unless `--telegram` is passed. AI Scanner and AI News
are read-only dashboard pages; set `AI_SCANNER_DB` when using a custom DB path.
The default scorer is an untrained, versioned heuristic with a validated LLM/ML
interface. Batch 3 creates two 1000 USDT virtual accounts, market+news and
market-only, each with at most one position and 10% sizing through the existing
RiskManager/PaperBroker. BUY requires trend/volume confirmation. Exits use 3%
stop, 6% take-profit, weak score or important negative news; stops are polling
based and can gap. Fees and slippage are inherited from PaperBroker.

Existing Batch 1/2 code and database are untouched. The systemd file in `deploy/`
is only an example; no server service is installed or restarted by this change.
Create the AI data directory before using the unit's `ReadWritePaths` restriction.
No live trading mode, API keys or Binance order endpoints are present in Batch 3.

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

## Strategy Optimizer

`StrategyOptimizer` перебирает переданную сетку параметров только на TRAIN (по
умолчанию 60% свечей). После выбора по TRAIN с прозрачным weighted score он запускает
лучшие параметры на VALIDATION (20%) и TEST (20%). Test-набор не участвует в подборе.

Score — это настраиваемая линейная комбинация: `return - max_drawdown + sharpe +
profit_factor`; веса каждого слагаемого передаются через `scoring_weights`, а стратегии
с числом сделок меньше `minimum_trade_count` исключаются. Profit factor ограничивается
значением 5 внутри score, чтобы одна серия без убытков не доминировала над оценкой.

```python
from app.core.historical_data import HistoricalDataProvider
from app.optimizer import StrategyOptimizer
from app.strategies.ema_strategy import EMAStrategy

candles = HistoricalDataProvider().get_history(
    symbol="BTCUSDT", interval="1h", start_date="2024-01-01", end_date="2024-06-30"
)
optimizer = StrategyOptimizer(
    backtest_config={"start_balance": 1000, "slippage": 0.0005},
    scoring_weights={"return": 1.0, "max_drawdown": 1.5, "sharpe": 2.0, "profit_factor": 1.0},
    minimum_trade_count=5,
)
result = optimizer.optimize(
    strategy_class=EMAStrategy,
    parameter_grid={"fast": [10, 15, 20, 25, 30], "slow": [40, 50, 60, 80, 100]},
    candles=candles,
    symbol="BTCUSDT",
    interval="1h",
)
print(result.best_parameters)
```

CSV и JSON со всеми комбинациями сохраняются в `data/optimization/`.

## Strategy suite и walk-forward

Индикаторы RSI, ATR, MACD и SMA работают с обычными списками (ATR требует свечи с
`high`, `low`, `close`). Новые стратегии: `RSIStrategy`, `EMARSIStrategy` и
`EMARSIATRStrategy`. Они принимают параметры в конструкторе и могут участвовать
в оптимизации. Бэктестер передаёт стратегии `MarketContext` с доступными на
текущий момент свечами. При оценке validation и test прошлые свечи прогревают
индикаторы, но сделки и метрики начинаются только с оцениваемого периода.

```python
from app.core.historical_data import HistoricalDataProvider
from app.optimizer import StrategyOptimizer, StrategyComparator
from app.strategies.ema_rsi_strategy import EMARSIStrategy
from app.strategies.rsi_strategy import RSIStrategy

candles = HistoricalDataProvider().get_history("BTCUSDT", "1h", "2024-01-01", "2024-12-31")
optimizer = StrategyOptimizer(minimum_trade_count=3)
grid = {"fast": [10, 20], "slow": [50, 80], "rsi_period": [7, 14]}
result = optimizer.optimize(EMARSIStrategy, grid, candles)
walk_forward = optimizer.walk_forward(EMARSIStrategy, grid, candles,
    train_size=2000, validation_size=500, step_size=500, anchored=False)
comparison = StrategyComparator().compare([
    (EMARSIStrategy, walk_forward.final_parameters), RSIStrategy
], candles[:int(len(candles) * 0.8)])
print(result.best_parameters, walk_forward.test_metrics, comparison.format_table())
```

`walk_forward` выбирает параметры отдельно в каждом train-окне, оценивает их на
следующем validation-окне, затем повторно выбирает по всей истории до финального
test. Test не участвует в выборе. `parameter_stability` показывает долю допустимых
соседних конфигураций со score не ниже 80% baseline, если baseline положителен;
это локальная эвристика, не проверка статистической значимости. Результаты
сохраняются в `data/optimization/` и `data/comparisons/` (CSV и JSON).

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

## Historical research (Milestone 6.5)

Run `python -m app.research.evaluation` to reproduce the fixed study protocol.
It caches public BTCUSDT and ETHUSDT spot candles (`1h` and `4h`) from
2021-01-01 through the last complete UTC day. The evaluation uses candles from
2023-09-01 onward, about three years. For each strategy and market, the inner
optimizer runs on the development portion only. Walk-forward uses moving train
and validation windows and leaves the final 20% for one final evaluation.

Reports are written to `data/research/<SYMBOL>_<INTERVAL>/` and the combined
table to `data/research/summary.csv` and `.json`. The same final holdout has
a gross Buy & Hold benchmark; its return, drawdown and Sharpe omit trading costs.
Strategy tests include commission and slippage. Parameter stability is a local
neighbor heuristic; diagnostic flags are prompts for investigation, not proof
of overfitting. Research uses the latest 1000 completed candles for indicator
calculation, so long-history EMA values are approximated by a rolling warm-up.

Once final test results are inspected, treat this holdout as used. Any later
parameter changes require a new, later untouched holdout for a fresh claim.

## Forward paper trading

Paper trading reads only Binance public closed candles. It has no API-key settings,
no trading client and no path to Binance order endpoints. SQLite is used at
`data/paper/paper.sqlite3`: it preserves experiment snapshots, trades and the last
processed candle atomically across restarts.

Create the six fixed forward experiments (1000 USDT each), then initialize them:

```powershell
python -m app.paper.run --create-initial
python -m app.paper.run
```

Run one polling process with `python -m app.paper.run --loop --interval 300`.
It processes each new closed candle once. To stop an experiment, call
`PaperStorage().set_status(experiment_id, "STOPPED")`; to restore it, set it to
`RUNNING`. Reports use `python -m app.paper.report <experiment_id>`.

The fixed parameters come from M6.5 development train/validation selection before
this forward period: BTC 1h EMA `(10, 50)`, BTC 1h EMA+RSI `(20, 50, 7, 30, 70)`,
BTC 1h EMA+RSI+ATR `(20, 50, 7, 30, 70, 14, 2, 3)`, BTC 4h EMA+RSI
`(10, 50, 7, 30, 70)`, ETH 1h EMA+RSI `(10, 50, 7, 30, 70)` and ETH 1h
EMA+RSI+ATR `(10, 50, 7, 30, 70, 14, 2, 3)`.

## Research Dashboard

The local dashboard uses Streamlit because it provides an uncomplicated, read-only
interface over the existing SQLite, CSV and JSON research artifacts. It does not
connect to exchange accounts, use API keys, or place orders.

```powershell
python -m pip install -r requirements.txt
streamlit run app/dashboard/dashboard.py
```

The dashboard includes paper-experiment performance, equity/drawdown/monthly-return
charts, trades, optimizer results and heatmaps, walk-forward stability, research
comparisons and Buy & Hold context. Each paper page can download CSV, JSON, HTML
and PDF reports. Dashboard warnings (few trades, drawdown, overfitting diagnostics
and negative expectancy) are prompts for investigation, not trading advice.

## Telegram notifications

Telegram is notification-only: it cannot send Binance orders and supports no chat
commands. Create a bot in Telegram with **@BotFather**, copy its token, then send a
message to the bot and obtain your numeric chat ID (for example through the Bot API
`getUpdates` response). Copy `.env.example` to `.env` and set:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

```powershell
python -m app.telegram.test
python -m app.paper.run --loop --interval 300
python -m app.telegram.daily_report
python -m app.telegram.weekly_report
```

Trade notifications are emitted only after a virtual trade is committed to SQLite.
Alerts use persisted per-key cooldowns; daily and weekly commands are lightweight
manual entry points, appropriate for OS Task Scheduler if automatic delivery is wanted.

## System Recovery

The paper runner stores the last processed closed candle in SQLite. After a computer
restart, process stop, temporary internet outage, or Binance outage, it requests the
available closed-candle history again and processes every candle after that checkpoint
in chronological order. A Telegram alert is best-effort and cooldown-protected; it
cannot block recovery or alter virtual trading logic. Startup prints SQLite, paper
storage, history, Telegram and Binance diagnostics.

## Reliability Monitoring

Telegram notifications are written to the local SQLite queue before delivery. The
runner retries with exponential backoff and drains queued messages in chronological
order after connectivity returns. The dashboard includes **System Status**,
**Telegram Queue**, and **Recovery History** pages. Runner startup prints elapsed
health diagnostics for Internet, Binance, SQLite, Telegram, history and recovery.

If Telegram is unavailable, check the queue and the token/chat ID; no paper trade or
recovery step is discarded because notification delivery fails.

## Batch 2 Multi-Asset Strategy Lab

Batch 1 remains the six baseline control experiments. Batch 2 adds 30 independent
paper experiments across BTC, ETH, SOL, BNB, XRP, LINK and DOGE on `15m`, `1h` and
`4h`. Every experiment has its own 1000 USDT virtual balance; balances are never
combined as a real portfolio. Strategies include Bollinger mean reversion, Donchian
breakout, MACD trend, Supertrend, ADX+EMA trend and RSI mean reversion.

Run the historical candidate evaluation with `python -m app.research.batch2`. It uses
only 2023-01-01 through 2025-12-31, with chronological 70/30 train-validation and no
reuse of the viewed M6.5 final holdout. Results are written to `data/research/batch_2/`.
After reviewing that output, initialize the idempotent paper batch with
`python -m app.paper.run --create-batch-2`. A repeat-safe SQLite backup is placed in
`data/paper/backups/`, and the exported configuration is stored at
`data/paper/batch_2_experiments.json`.
