# Milestone 10 delivery report

This is the initial implementation report. The subsequent local-only completion,
manual PAPER run and current tests are documented in [BATCH3_LOCAL.md](BATCH3_LOCAL.md).

Implemented against `C:\Users\ostap\PycharmProjects\Al_Trader`, using an isolated
working copy for development. No git commit, server deployment, live order or
Telegram message was made.

## Changed existing files

- `app/__init__.py`: lazy Application export; importing paper/research modules
  no longer initializes the legacy Binance client.
- `app/dashboard/dashboard.py`: adds AI Scanner and AI News page registration.
- `README.md`: Batch 3 commands, isolation and operating limits.

## Added files

- `app/ai/__init__.py`: isolated namespace.
- `app/ai/news.py`: bounded HTTPS RSS/Atom collection, timestamps, URL/title
  dedup keys, conservative symbol resolver, scorer protocol and strict schema.
- `app/ai/market.py`: public GET-only Binance adapter, liquid USDT universe,
  closed 15m/1h/4h trend/momentum/volume/volatility/ATR features.
- `app/ai/scoring.py`: structured news assessments and versioned opportunity
  ranking with BUY/WATCH/PASS and market confirmation.
- `app/ai/storage.py`: isolated marked SQLite, immutable first-seen ingestion,
  scan/account/trade persistence and read-only dashboard queries.
- `app/ai/runner.py`: forward decisions, post-decision quotes, existing
  RiskManager/PaperBroker execution, transactional account/fill/outbox storage,
  restart/concurrency idempotence and protective exits.
- `app/ai/run.py`: scanner-only default; explicit paper and Telegram switches.
- `app/dashboard/ai_pages.py`: rankings, features, news, health, accounts,
  executions and queue status.
- `deploy/ai-scanner.service.example`: separate optional service template.
- `tests/test_ai.py`: 37 additional test cases.
- `docs/MILESTONE_10.md`: architecture, migration contract and limitations.
- `docs/M10_REPORT.md`: this report.

## Validation

Full pytest: **88 passed**, including 51 existing tests and 37 new cases.
Three deprecation warnings originate from the legacy Binance/websockets dependency.
New tests cover unavailable/future/stale inputs, feed safety, deduplication,
symbol ambiguity, malformed model output, liquidity, no-look-ahead, isolation,
risk rejection, stops, restart, concurrent runners, rollback, queue failures,
read-only Streamlit pages and absence of exchange client imports.

Public source smoke: 55 articles parsed (25 CoinDesk, 30 Cointelegraph); public
Binance universe request succeeded. A complete one-symbol scanner-only cycle
using a temporary database ranked one market with zero errors. It had zero
eligible current news assessments; feed retrieval does not imply fresh usable
news at every scan. No real paper experiment state was initialized by this smoke.

## Batch 3

Independent `batch_3_news` and `batch_3_market` virtual accounts, 1000 USDT each,
created on the first explicit `--paper` scan. At most one position per account;
10% balance sizing, inherited risk checks/fees/slippage. 3% stop, 6% take-profit,
weak score and material negative-news exits. Snapshot comparisons persist per scan.
Default storage: `data/ai/batch3.sqlite3`. Legacy `data/paper/paper.sqlite3` is
never opened by the AI runner; existing non-AI databases are rejected.

From the project directory:

```powershell
.\.venv\Scripts\python.exe -m app.ai.run
.\.venv\Scripts\python.exe -m app.ai.run --paper --loop
```

The first command only scans. The second explicitly starts forward virtual
trading. Add `--telegram` only when notification delivery is desired.

## Limits / not claimed

- Default scoring is an untrained English headline heuristic, with a structured
  interface for future LLM/ML adapters. No external model or trained predictor
  is connected; score is not a profit probability.
- Exact title/URL deduplication is not semantic cross-source story clustering.
- Forward-only evaluation: no retrospective fills with current news/universe.
- No automatic server rollout; local/server version parity remains unverified.
  Existing systemd deployment was not supplied and remains untouched.
- Batch 3 appears in AI pages, not legacy Batch Comparison, to avoid changing
  the old runner and experiment registry.
- The inherited loss limit is cumulative despite its daily name. Polling stops
  may gap; unavailable quotes prevent execution. Depth and lot-size simulation,
  trained models, calibration and long-term retention are not implemented.
- Telegram delivery retains the existing at-least-once semantics. No messages
  were sent during implementation or tests.
- Original pre-existing modified PDF report is outside this change.
