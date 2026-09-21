from dataclasses import dataclass
import os
from dotenv import load_dotenv


@dataclass(frozen=True)
class TelegramSettings:
    token: str | None
    chat_id: str | None
    timeout: float = 10.0
    alert_cooldown_seconds: int = 3600
    negative_expectancy_minimum_trades: int = 10
    drawdown_threshold: float = 20.0

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    @classmethod
    def from_environment(cls):
        load_dotenv()
        return cls(os.getenv("TELEGRAM_BOT_TOKEN") or None, os.getenv("TELEGRAM_CHAT_ID") or None)
