# M20.4 — Stage A Public Market Data Smoke Capture Protocol

## Frozen execution identity

- Status sought: `SMOKE_CAPTURE_PASSED`, `PARTIAL`, `FAILED`, or `BLOCKED_BEFORE_CAPTURE` according to the observed result.
- Source and remote checkpoint: `4e4308ad5bea00a55b593581b546ffb872ed07d1`.
- Branch: `main`.
- Execution checkout: `/home/ostap/ai_trader_m20_4_4e4308a_exact`.
- Python: `/home/ostap/ai_trader_wsl_c497d40e/.venv/bin/python`, Python 3.14.4.
- Raw output: `/home/ostap/ai_trader_capture/stage_a_20261011T211113Z`.
- Run ID: `stage_a_20261011T211113Z`.

The execution checkout is a detached local clone of the published commit with Git line-ending conversion disabled. All ten `app/research/market_capture/*.py` blob IDs match the source commit. No `.env` is present in the execution checkout.

## Executed source SHA-256

| File | SHA-256 |
|---|---|
| `__init__.py` | `1340633ef904310b6a3274acb6c269ce8bccda0516cb9916e8f1991415d309bf` |
| `__main__.py` | `eef409e9e3910ac8779859194955ec87c6ba633c92d89854decfc46d4b1eb1f2` |
| `book.py` | `21a26c7bb543b412e668ca1c578e44510f4a56d09072507bc48ec14e6414ebde` |
| `engine.py` | `230eb541a90dcd3866976b0151530af3d14c7fa1c551cb6c1b6d636e9de01c99` |
| `envelope.py` | `6e12a63c168a4ced6f86736433b02b22fc2d3f6277a88d9a57d9443e3c8b3272` |
| `public_adapter.py` | `8ff7636149f36c9a7dd57b60ae47bfa51e65dcc8066913ea9207302850630f86` |
| `queueing.py` | `a6dd3a7f23ccfd039d2c7798a26ed814e98c5f6f42775c3df098724b4bd3ad83` |
| `replay.py` | `9296915f85d2953980cc45e14727be399091b020b359016cf7e3c0d6ba9a8f2a` |
| `storage.py` | `9ecb7f0e39487232cc55308856c827f8becd961fcea75392fa0b327a82bd31a7` |
| `transport.py` | `6ae435a88bb4223b09c89d9a12a50d8ba6e4e61f28ec5113bfb645c8e9597cc4` |

## Effective configuration

- Symbol: `BTCUSDT` only.
- WebSocket streams: `trade`, `bookTicker`, and `depth@100ms`.
- Public REST depth snapshots with 5000 levels for initialization and bounded recovery.
- Target active collection duration: 1800 seconds.
- Total run-file cap: 2,147,483,648 bytes.
- Minimum free space after an accepted write: 4,294,967,296 bytes.
- Finalization reserve: 1,048,576 bytes.
- Queue capacity: 4096; WebSocket `max_queue` uses the same bound.
- Maximum application record and bounded REST body: 2,097,152 bytes.
- Snapshot timeout: 10 seconds; at most 3 HTTP attempts per snapshot operation.
- Recovery limit: 3; reconnect limit: 3.
- Connect timeout: 10 seconds; receive polling: 5 seconds.
- WebSocket close cleanup budget: 2 seconds.

Offline preflight reported 1,023,198,232,576 bytes available inside WSL and required at least 6,442,450,944 bytes. The WSL virtual disk is `C:\Users\ostap\AppData\Local\wsl\{8d84dacf-bf6a-44d6-bb46-d3b3cdd06218}\ext4.vhdx`; its Windows NTFS volume had 132,135,628,800 bytes free.

## Exact command

```bash
cd /home/ostap/ai_trader_m20_4_4e4308a_exact
PYTHONDONTWRITEBYTECODE=1 /home/ostap/ai_trader_wsl_c497d40e/.venv/bin/python \
  -m app.research.market_capture capture \
  --output-root /home/ostap/ai_trader_capture \
  --run-id stage_a_20261011T211113Z \
  --duration-seconds 1800 \
  --confirm PUBLIC_MARKET_CAPTURE_AUTHORIZED
```

Only one capture process may be started. Built-in bounded reconnect, retry and resnapshot remain part of that single run. The run stops on elapsed-budget completion, retry/recovery exhaustion, storage cap, minimum-free-space violation, record-size violation, writer failure or explicit cancellation. Raw data is create-only and must not be repaired or supplemented.

## Measurement and analysis boundary

Monitoring may sample process state, RSS, CPU, file growth, free space, reconnects and recovery. Sparse OS samples are observations, not exact peaks. Post-run analysis is limited to transport and data quality: schemas, counts/rates, bytes, known local drops, gaps, recovery, book-validity coverage, exact-ID comparison, integrity and replay.

No PnL, trading return, predictive relationship, labels, model, signal, feature/horizon search or threshold optimization is permitted. No account data, key, order, private endpoint, Stage B, Batch 3, Oracle, server, SQLite or trading process is involved.

## Timing limitation

Requests connect/read timeouts are not a total wall-clock HTTP deadline. A slowly arriving response body can extend execution. The adapter waits for a blocking HTTP worker after cancellation so it does not report completion while hidden HTTP work remains, but that wait has no proven fixed return time. The 1800-second setting is the target active-capture limit rather than a demonstrated upper bound on process lifetime. The two-second WebSocket cleanup budget is not a global HTTP shutdown deadline. A successful Stage A run cannot prove the absence of rare future stalls.
