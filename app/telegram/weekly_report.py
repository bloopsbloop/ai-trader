from app.paper.storage import PaperStorage
from app.telegram.reports import send_weekly, weekly_report_chunks

if __name__ == "__main__":
    storage = PaperStorage()
    count = len(weekly_report_chunks(storage))
    sent = send_weekly(storage)
    print(f"Sent successfully ({count} chunks)" if sent else f"Not sent: {storage.notification_state().get('last_error', 'Telegram is not configured')}")
