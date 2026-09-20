from datetime import datetime


def log(message: str):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")