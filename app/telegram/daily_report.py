from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier
from app.telegram.reports import send_daily

if __name__ == "__main__":
    storage = PaperStorage()
    sent = send_daily(storage)
    print("Sent successfully" if sent else f"Not sent: {storage.notification_state().get('last_error', 'Telegram is not configured')}")
