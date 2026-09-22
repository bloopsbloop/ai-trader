import argparse
import time

from app.paper.experiments import create_batch_2_experiments, create_initial_experiments
from app.paper.runner import PaperRunner
from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier
from app.telegram.diagnostics import format_checks, startup_checks


def main():
    started_at = time.monotonic()
    parser = argparse.ArgumentParser(description="Forward paper trading on closed public Binance candles")
    parser.add_argument("--create-initial", action="store_true")
    parser.add_argument("--create-batch-2", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300, help="Polling interval in seconds (minimum 60)")
    args = parser.parse_args()
    storage = PaperStorage()
    notifier = TelegramNotifier(storage)
    if args.create_initial:
        for experiment in create_initial_experiments(storage):
            print(f"Created {experiment.experiment_id} {experiment.symbol} {experiment.timeframe} {experiment.strategy}")
    if args.create_batch_2:
        for experiment in create_batch_2_experiments(storage):
            print(f"Ready {experiment.experiment_id} {experiment.symbol} {experiment.timeframe} {experiment.strategy}")
    if (args.create_initial or args.create_batch_2) and not args.loop:
        return
    runner = PaperRunner(storage, notifier=notifier)
    print(format_checks(startup_checks(storage, runner.market_data, notifier)))
    print(f"Startup diagnostics completed in {time.monotonic() - started_at:.2f}s")
    while True:
        try:
            notifier.flush_queue()
            for message in runner.run_once():
                print(message)
        except Exception:
            notifier.alert("paper_runner_exception", "Paper runner exception. Check local logs.")
        if not args.loop:
            return
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()

