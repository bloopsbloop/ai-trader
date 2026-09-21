from pathlib import Path
import requests


class TelegramClient:
    def __init__(self, settings, session=requests):
        self.settings = settings
        self.session = session

    def send_message(self, text):
        if not self.settings.configured:
            return False, "Telegram is not configured"
        try:
            response = self.session.post(f"https://api.telegram.org/bot{self.settings.token}/sendMessage", json={"chat_id": self.settings.chat_id, "text": text}, timeout=self.settings.timeout)
            data = response.json()
            if not response.ok or not data.get("ok"):
                return False, "Telegram API rejected notification"
            return True, None
        except (requests.RequestException, ValueError):
            return False, "Telegram network request failed"

    def send_document(self, path):
        return self._send_file("sendDocument", "document", path)

    def send_photo(self, path):
        return self._send_file("sendPhoto", "photo", path)

    def _send_file(self, method, field, path):
        if not self.settings.configured:
            return False, "Telegram is not configured"
        try:
            with Path(path).open("rb") as file:
                response = self.session.post(f"https://api.telegram.org/bot{self.settings.token}/{method}", data={"chat_id": self.settings.chat_id}, files={field: file}, timeout=self.settings.timeout)
            return (True, None) if response.ok and response.json().get("ok") else (False, "Telegram API rejected notification")
        except (OSError, requests.RequestException, ValueError):
            return False, "Telegram network request failed"
