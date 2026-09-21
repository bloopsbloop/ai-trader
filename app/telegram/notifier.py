from datetime import datetime, timezone

from app.telegram.client import TelegramClient
from app.telegram.formatter import trade_message
from app.telegram.settings import TelegramSettings


class TelegramNotifier:
    def __init__(self, storage, settings=None, client=None):
        self.storage = storage
        self.settings = settings or TelegramSettings.from_environment()
        self.client = client or TelegramClient(self.settings)

    def send(self, text):
        success, error = self.client.send_message(text)
        self.storage.notification_result(success, error)
        return success

    def notify_trade(self, experiment, order, state, equity, entry=None):
        return self.send(trade_message(experiment, order, state, equity, entry))

    def alert(self, key, text):
        if not self.storage.claim_alert(key, self.settings.alert_cooldown_seconds):
            return False
        return self.send("⚠️ PAPER ALERT\n\n" + text)

    def status(self):
        state = self.storage.notification_state()
        return {"configured": self.settings.configured, "last_success": state.get("last_success"), "last_error": state.get("last_error")}
