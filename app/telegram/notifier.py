import time

from app.telegram.client import TelegramClient
from app.telegram.formatter import trade_message
from app.telegram.settings import TelegramSettings


class TelegramNotifier:
    def __init__(self, storage, settings=None, client=None):
        self.storage = storage
        self.settings = settings or TelegramSettings.from_environment()
        self.client = client or TelegramClient(self.settings)

    def send(self, text, reason="notification"):
        item_id = self.storage.queue_notification(text, reason)
        return self.flush_queue(target_id=item_id)

    def flush_queue(self, target_id=None):
        delivered = False
        target_delivered = False
        for item in self.storage.pending_notifications():
            success, error = False, None
            for attempt in range(self.settings.retry_count):
                success, error = self.client.send_message(item["text"])
                if success:
                    break
                if attempt + 1 < self.settings.retry_count:
                    time.sleep(self.settings.retry_backoff_seconds * (2 ** attempt))
            self.storage.notification_result(success, error)
            if success:
                self.storage.complete_notification(item["id"], self.client.last_message_id)
                delivered = True
                target_delivered = target_delivered or item["id"] == target_id
            elif error and error.startswith("Telegram API rejected notification:"):
                self.storage.fail_notification(item["id"], error)
            else:
                self.storage.retry_notification(item["id"], error)
                break
        return target_delivered if target_id is not None else delivered

    def notify_trade(self, experiment, order, state, equity, entry=None):
        return self.send(trade_message(experiment, order, state, equity, entry), "trade")

    def alert(self, key, text):
        if not self.storage.claim_alert(key, self.settings.alert_cooldown_seconds):
            return False
        return self.send("⚠️ PAPER ALERT\n\n" + text, key)

    def status(self):
        state = self.storage.notification_state()
        pending = len(self.storage.pending_notifications())
        health = "Connected" if state.get("last_success") and not pending else ("Queue pending" if pending else ("Configured but unreachable" if self.settings.configured else "Not configured"))
        return {"configured": self.settings.configured, "health": health, "last_success": state.get("last_success"), "last_error": state.get("last_error"), "pending": pending}
