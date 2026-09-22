from app.paper.storage import PaperStorage
from app.telegram.notifier import TelegramNotifier
from app.telegram.reports import daily_report_chunks, send_daily

if __name__ == "__main__":
    storage = PaperStorage()
    count = len(daily_report_chunks(storage))
    sent = send_daily(storage)
    print(f"Sent successfully ({count} chunks)" if sent else f"Not sent: {storage.notification_state().get('last_error', 'Telegram is not configured')}")
