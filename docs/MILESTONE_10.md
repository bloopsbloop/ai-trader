# Milestone 10 — migration contract

Source inspected: `C:/Users/ostap/PycharmProjects/Al_Trader`. The ChatGPT mirror
contains an obsolete Core; implementation targets the local `app` project.
No existing database, secrets, deployment files, or experiments are copied.

## Architecture and rollout

1. Preserve Batch 1/2 runner, experiment definitions, broker and storage schema.
2. Add `app.ai`: public data -> timestamped observations -> closed-candle features
   and structured news assessments -> ranking -> Batch 3 -> existing PaperBroker
   and RiskManager. No exchange trading client is imported by this path.
3. Use a separate `data/ai/batch3.sqlite3`, identified by an application marker.
   Refuse a nonempty unmarked database (including Batch 1/2). Transactions include
   scan, portfolio, trades and Telegram outbox; a unique 15m slot prevents replay.
4. Start in scanner-only mode. Explicit `--paper` enables two independent 1000 USDT
   research portfolios, `batch_3_news` and `batch_3_market`, one position each.
   Both share observations and costs; only the first uses news. Rank liquid spot
   USDT markets; fetch held symbols even after they leave the ranked universe.
5. Dashboard adds read-only AI Scanner and AI News pages. Telegram reuses the
   existing retry queue implementation on the separate database. Only filled
   BUY/SELL and deduplicated major news enter that queue; delivery is opt-in.
6. Deploy a separate systemd service only after comparing the local code with the
   server version and backing up server state using SQLite's backup API. The
   supplied unit is a template, not an installed or enabled service. Rollback:
   stop only the AI service; retain its database for audit. Batch 1/2 continues.

## Time and scoring contract

UTC everywhere. Publication time and first-seen time are distinct; no article
can influence an earlier decision. Candles must have closed before the scan
cutoff; missing, duplicate, nonfinite or stale bars invalidate that symbol.
The universe is a recorded current observation, never historical reconstruction.
Execution uses a fresh quote obtained after the decision, with broker slippage
and fees; no retrospective fill at the feature candle's close. Downtime is not
backfilled with today's news/universe. Forward paper evaluation only.

News scores contain sentiment [-1,1], importance/freshness/confidence [0,1],
event_type and scorer_version. The default is an explicit deterministic baseline,
NOT a trained AI and NOT a probability of profit. The scorer protocol accepts a
future LLM/ML adapter; strict validation fails closed on invalid outputs. Article
text is data and never grants tools or execution authority. Freshness is derived
from timestamps, not trusted model output. Model training, calibration, provider
credentials and prompt evaluation are follow-up work.

Weighted market/news scoring and thresholds are fixed experimental heuristics.
News cannot trigger BUY without market confirmation. Unknown aliases, ambiguous
bare ticker words, unsupported languages and rumors have conservative handling.
Exact URL/title deduplication does not claim semantic story clustering.

## Acceptance

Run the entire existing pytest suite plus new tests: no future/late news leakage,
closed bars, malformed feeds/scores, liquidity, exits, restart idempotence,
transaction rollback, separate database, queue failure and read-only dashboards.
No profitability claims are made from unit tests. Observe forward results before
changing thresholds; comparisons are descriptive, not a causal proof.

Public market endpoint reference:
https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market
RSS endpoints are configurable; failures are visible and never silently treated
as healthy news coverage.

## Operational limits

No server access or systemd unit was present in the local source; server parity
and deployment are unverified. Batch 3 is deliberately absent from the legacy
runner's experiment registry: its two accounts appear on AI Scanner, not in the
old Batch Comparison page. Every scan saves both account snapshots for later
comparison. Paper simulation omits depth/partial fills, exchange lot-size filters
and funding (spot only). Universe volume filtering is a liquidity proxy.
The inherited risk manager's loss limit uses cumulative realized PnL, despite its
daily name; Batch 3 does not reset it or change Batch 1/2 risk behavior.
Missing feature history still allows quote-based protective exits for held assets.
If quotes fail, positions remain open and the error is recorded; stops are not
guaranteed. Telegram has at-least-once transport semantics (a crash after send but
before acknowledgement can duplicate delivery), while outbox creation is atomic
with fills. Queue records accumulate even when transport is disabled.
No automatic retention/compaction policy is enabled; monitor database disk usage.
