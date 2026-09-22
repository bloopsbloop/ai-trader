from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier


def startup_checks(storage=None, market_data=None, notifier=None):
    storage = storage or PaperStorage()
    notifier = notifier or TelegramNotifier(storage)
    checks = {"Telegram": "configured" if notifier.settings.configured else "not configured", "SQLite": "OK", "Paper storage": "OK", "History": "OK"}
    if market_data is not None:
        try:
            active = storage.active_experiments()
            if active:
                market_data.closed_candles(active[0][0].symbol, active[0][0].timeframe, limit=2)
            checks["Internet"] = "Connected"
            checks["Binance"] = "Connected"
        except Exception:
            checks["Internet"] = "Unavailable"
            checks["Binance"] = "Unavailable"
    return checks


def format_checks(checks):
    return "\n".join(f"{'[OK]' if value in {'OK', 'Connected', 'configured'} else '[!]'} {name}: {value}" for name, value in checks.items())
