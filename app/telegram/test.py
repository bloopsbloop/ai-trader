from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier

if __name__ == "__main__":
    print("sent" if TelegramNotifier(PaperStorage()).send("✅ AI Trader Telegram notifications are working.") else "not sent; configure Telegram first")
