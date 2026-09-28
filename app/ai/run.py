"""python -m app.ai.run [--paper] [--loop] [--telegram]."""
import argparse
import json
import logging
import time
import os
from logging.handlers import RotatingFileHandler
from app.ai.news import NewsCollector
from app.ai.runner import AIRunner
from app.ai.storage import AIStorage


def run_cycles(runner, notifier=None, interval=900, cycles=1, sleep=time.sleep):
    """None cycles runs until Ctrl+C. Injectable sleep supports deterministic tests."""
    completed = 0
    failures = 0
    pid = os.getpid()
    try:
        while cycles is None or completed < cycles:
            runner.storage.heartbeat("RUNNING", pid=pid, paper=runner.paper, cycles=completed)
            try:
                scan = runner.run_once()
                summary = {key: scan.get(key) for key in ("slot", "mode", "already_processed", "errors")}
                print(json.dumps(summary), flush=True)
                logging.info("Cycle %s: %s", completed + 1, json.dumps(summary))
                runner.storage.heartbeat("WAITING", pid=pid, paper=runner.paper, cycles=completed + 1, **summary)
            except Exception as exc:
                failures += 1
                logging.exception("AI scan failed; no partial portfolio transaction committed")
                runner.storage.heartbeat("ERROR", pid=pid, error=type(exc).__name__, cycles=completed + 1)
                if cycles == 1:
                    raise
            if notifier is not None:
                try:
                    notifier.flush_queue()
                except Exception:
                    logging.exception("AI Telegram delivery failed; persisted queue retained")
            completed += 1
            if cycles is None or completed < cycles:
                sleep(interval)
    except KeyboardInterrupt:
        runner.storage.heartbeat("STOPPED", pid=pid, cycles=completed, failures=failures, reason="keyboard interrupt")
        logging.info("Batch 3 stopped by user")
    else:
        runner.storage.heartbeat("STOPPED", pid=pid, cycles=completed, failures=failures, reason="requested cycles completed")


def main():
    parser = argparse.ArgumentParser(description="Batch 3 PAPER research; no live mode exists")
    parser.add_argument("--paper", action="store_true", help="Enable virtual fills; otherwise scanner only")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--cycles", type=int, help="Stop after N cycles; useful for local validation")
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument("--db", help="Separate AI database; legacy paper databases are refused")
    parser.add_argument("--feed", action="append", help="HTTPS RSS/Atom URL; repeat for multiple sources")
    parser.add_argument("--telegram", action="store_true", help="Deliver the AI outbox using existing Telegram settings")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("Minimum polling interval is 60 seconds")
    if args.cycles is not None and (args.cycles < 1 or not args.loop):
        parser.error("--cycles requires --loop and a positive integer")
    storage = AIStorage(args.db)
    handler = RotatingFileHandler(storage.path.parent / "runner.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(message)s")
    runner = AIRunner(storage=storage, collector=NewsCollector(args.feed), paper=args.paper)
    notifier = None
    if args.telegram:
        from app.telegram.notifier import TelegramNotifier
        notifier = TelegramNotifier(storage)
    run_cycles(runner, notifier, args.interval, args.cycles if args.loop else 1)


if __name__ == "__main__":
    main()
