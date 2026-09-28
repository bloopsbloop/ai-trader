# Batch 3 local validation — 2026-09-28

Scope: `C:\Users\ostap\PycharmProjects\Al_Trader` only. No Oracle connection,
server deployment, systemd/nginx changes, .env edits, server SQLite access,
live Binance trading, Telegram sends or git commit.

## Local commands

Run these from the project directory in PowerShell:

```powershell
# Continuous paper runner; stop cleanly with Ctrl+C.
.\.venv\Scripts\python.exe -B -m app.ai.run --paper --loop

# Bounded local validation; the interval controls polling, decisions remain 15m.
.\.venv\Scripts\python.exe -B -m app.ai.run --paper --loop --interval 60 --cycles 2

# Standalone dashboard, bound to this machine only.
.\.venv\Scripts\python.exe -B -m streamlit run app/ai/dashboard.py --server.address 127.0.0.1 --server.port 8513 --server.headless true --browser.gatherUsageStats false

# Tests.
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

The dashboard is at http://127.0.0.1:8513 while its command runs. It reads only
Batch 3 and never initializes the Batch 1/2 database. Use Refresh to reload state.

`--paper` explicitly enables virtual fills. Without it the runner only scans.
`--telegram` explicitly enables delivery using existing local Telegram settings;
no .env changes are required by this code. The local validation did NOT enable
it: two notifications remain in the local queue. Enabling transport later may
deliver these pending validation notifications. Delivery is at-least-once across
an external-send/acknowledgement crash; queue insertion is atomic with each fill.

## Persistence and accounts

- SQLite: `data/ai/batch3.sqlite3`, separate from Batch 1/2.
- Rotating log: `data/ai/runner.log` (2 MB, three backups).
- Runner heartbeat: `ai_runtime`, including status, PID, last update and failures.
- Portfolios: `with_news` and `without_news`, each initialized with 1000 USDT.
  Existing database IDs `batch_3_news` / `batch_3_market` are retained; the display
  names and stored `portfolio` fields identify their roles without resetting state.
- News first-seen/publication times, source health, rankings, features, decisions,
  account snapshots, fills and notification queue survive restarts.

Repeated polls in the same 15m slot do not repeat network scans or fills. A
transaction and unique slot protect concurrent attempts. GET retries are bounded
to two; 418/429 enter source-local cooldown using Retry-After. Each RSS source
has a separate session. Slow scans suppress new entries while fresh quotes can
still trigger protective exits for existing positions. Unavailable quotes leave
the virtual position open with a recorded error. No historic news backfill is
used to execute retrospective trades.

## Manual evidence

Executed the two-cycle local command above, then restarted with `--paper`:

- First cycle: PAPER, 30 eligible USDT markets, no scan errors.
- Ingestion: 55 RSS articles (25 CoinDesk, 30 Cointelegraph), two eligible news
  assessments in the scan.
- Each ranking: 1 BUY, 8 WATCH, 21 PASS.
- Two virtual BUY fills, one per independent portfolio (GRAMUSDT).
- Each account after fill: cash 899.96 USDT, commission 0.04 USDT;
  simulated entry 1.646823, quantity about 60.72297994.
- Second cycle and process restart: `already_processed=true`, still two fills.
- Two Telegram messages queued; zero sent.
- Standalone Streamlit started on loopback and its health endpoint returned 200;
  both page renderings are also covered by AppTest.
- Validation processes stopped after completion. The virtual positions remain
  in SQLite for continuation; no background runner is left managing them.

## Tests and limitations

Final pytest: **101 passed**, three existing Binance/websockets deprecation
warnings. New coverage includes bounded loops, next-slot progress, recovery,
Ctrl+C, rate-limit suppression, transient retry recovery, source isolation,
stale-scan exits, dashboard rendering and mocked Telegram success/failure.

Scoring remains a versioned, untrained English-headline/market heuristic with a
validated LLM/ML-compatible interface. It is not a connected LLM, trained model,
profit probability or evidence of profitable trading. Paper fills approximate
fees/slippage, omit depth/partial fills and lot sizes; stops depend on polling.
The inherited risk loss limit remains cumulative despite its daily name.
No server readiness or deployment claim is made.

Files completed in this local pass: `app/ai/http.py`, `market.py`, `news.py`,
`runner.py`, `run.py`, `storage.py`, `dashboard.py`, `app/dashboard/ai_pages.py`,
`tests/test_ai_local.py`, and local documentation. Existing Batch 1/2 modules,
the pre-existing PDF change and deployment template were left untouched.
