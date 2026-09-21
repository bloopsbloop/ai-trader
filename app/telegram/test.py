from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier


def main():
    storage = PaperStorage()
    sent = TelegramNotifier(storage).send("✅ AI Trader Telegram notifications are working.")
    print("Sent successfully" if sent else f"Not sent: {storage.notification_state().get('last_error', 'Telegram is not configured')}")


if __name__ == "__main__":
    main()
