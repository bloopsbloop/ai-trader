from app.telegram.reports import send_weekly

if __name__ == "__main__":
    print("sent" if send_weekly() else "not sent; configure Telegram first")
