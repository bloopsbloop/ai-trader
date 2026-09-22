from pathlib import Path
import requests


class TelegramClient:
    def __init__(self, settings, session=requests):
        self.settings = settings
        self.session = session
        self.last_message_id = None

    def send_message(self, text):
        if not self.settings.configured:
            return False, "Telegram is not configured"
        try:
            response = self.session.post(f"https://api.telegram.org/bot{self.settings.token}/sendMessage", json={"chat_id": self.settings.chat_id, "text": text}, timeout=self.settings.timeout)
            data = response.json()
            if data.get("ok") is not True:
                description = data.get("description") or "Unknown Telegram API error"
                return False, f"Telegram API rejected notification: {description}"
            self.last_message_id = data.get("result", {}).get("message_id")
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
            data = response.json()
            if data.get("ok") is True:
                self.last_message_id = data.get("result", {}).get("message_id")
                return True, None
            return False, f"Telegram API rejected notification: {data.get('description') or 'Unknown Telegram API error'}"
        except (OSError, requests.RequestException, ValueError):
            return False, "Telegram network request failed"
