# M20.3 — Stage A Runbook (Not Executed)

## Authorization boundary

**The commands in this runbook were not executed in M20.3.** M20.3 used fake HTTP/WebSocket clients only. Running the capture command opens public Binance market-data connections and requires a separate explicit Stage A authorization, confirmation that the computer may remain awake and connected, and acceptance of the disk budget.

## Required operator decisions

Before Stage A, choose and confirm:

1. A local Linux-filesystem output root. Recommended example: `/home/ostap/ai_trader_capture`. Do not use the project repository, `.env`, SQLite or an existing research namespace.
2. A unique run ID such as `stage_a_20261011T120000Z`; an existing directory is rejected.
3. At least **6 GiB free at start**: 2 GiB maximum run files plus 4 GiB minimum remaining free-space margin.
4. Permission for up to **30 minutes** of uninterrupted runtime, network access and host wakefulness.
5. Review of the two fixed public endpoints in `docs/M20_3_PROTOCOL.md`. No key or account access is required.

## Offline preflight

From the verified WSL project environment:

```bash
cd /home/ostap/ai_trader_wsl_c497d40e
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.research.market_capture preflight \
  --output-root /home/ostap/ai_trader_capture \
  --run-id stage_a_20261011T120000Z \
  --duration-seconds 1800
```

Preflight performs filesystem/configuration checks only. Expected output includes:

- `network_performed: false`;
- exact output path;
- 1800-second deadline;
- 2,147,483,648-byte cap;
- 4,294,967,296-byte minimum free space after writes;
- 6,442,450,944-byte minimum free space at start;
- `READY_FOR_SEPARATE_LIVE_AUTHORIZATION`.

A failed free-space, existing-directory or configuration check must be resolved through a new operator decision. Do not delete prior runs automatically.

## Future capture command

Run only after separate Stage A authorization:

```bash
cd /home/ostap/ai_trader_wsl_c497d40e
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.research.market_capture capture \
  --output-root /home/ostap/ai_trader_capture \
  --run-id stage_a_20261011T120000Z \
  --duration-seconds 1800 \
  --confirm PUBLIC_MARKET_CAPTURE_AUTHORIZED
```

A dry-run of the same command can add `--dry-run`; it performs preflight and exits without network access.

## Expected files and statuses

A successfully finalized run contains:

- `raw.jsonl`: application-level WebSocket payloads, raw REST snapshot bodies and lifecycle control records;
- `report.json`: capture-side state, counters, durations and transport telemetry;
- `manifest.json`: effective config, schemas, raw/report sizes and hashes;
- `manifest.sha256`: finalization marker.

Only a matching marker, manifest, raw and report constitute a finalized run. Missing marker means `RECOVERY_UNFINALIZED`. Offline replay command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.research.market_capture replay \
  /home/ostap/ai_trader_capture/stage_a_20261011T120000Z
```

`VERIFIED_FINALIZED` confirms local hashes and deterministic reconstruction. It does not confirm exchange completeness or trading value.

## Stop and error behavior

- Normal deadline: stops at 30 minutes independently of market-message arrival.
- User cancellation requested through the adapter lifecycle writes cancellation/stop controls when storage remains writable, closes pending tasks and the WebSocket, then finalizes.
- A process-level interruption such as power loss or forced termination may leave no final marker; preserve the directory and use recovery replay. Do not edit or truncate raw data.
- Disconnect: records the cause, closes the old session, applies bounded backoff and starts a new continuity epoch with a fresh snapshot. Missing history is not repaired.
- HTTP timeout/429/418/5xx: bounded retry; exhaustion stops recovery.
- Queue saturation or oversized input: records a known local loss, invalidates the book and requires reconnect/resnapshot.
- Cap/free-space stop: no automatic deletion. Finalization occurs only if the reserved space is sufficient.
- Writer/flush/fsync failure: the run remains incomplete; preserve it for diagnosis.

After any Stage A run, stop and review schema, resource use, gaps, recovery and integrity. There is no automatic Stage B transition.
