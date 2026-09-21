from app.telegram.reports import send_daily

if __name__ == "__main__":
    print("sent" if send_daily() else "not sent; configure Telegram first")
