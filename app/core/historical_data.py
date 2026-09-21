import csv
from datetime import date, datetime, time, timezone
from pathlib import Path

from app.core.exchange import client as default_client
from app.models.candle import Candle


class HistoricalDataProvider:
    """Downloads Binance candles and caches them locally as CSV files."""

    SUPPORTED_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
    CSV_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")

    def __init__(self, client=None, storage_dir=None):
        self.client = client or default_client
        project_root = Path(__file__).resolve().parents[2]
        self.storage_dir = Path(storage_dir or project_root / "data" / "history")

    def download(self, symbol, interval, start_date, end_date):
        self._validate_interval(interval)
        start = self._to_datetime(start_date)
        end = self._to_datetime(end_date, end_of_day=True)
        if end < start:
            raise ValueError("End date must not be before start date")

        klines = self.client.get_historical_klines(
            symbol=symbol.upper(),
            interval=interval,
            start_str=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_str=end.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return [self._to_candle(kline) for kline in klines]

    def save_csv(self, candles, symbol, interval):
        self._validate_interval(interval)
        path = self._cache_path(symbol, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()
            for candle in candles:
                writer.writerow(
                    {
                        "open_time": candle.open_time.isoformat(),
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                    }
                )
        return path

    def load_csv(self, symbol, interval):
        self._validate_interval(interval)
        path = self._cache_path(symbol, interval)
        if not path.exists():
            return []

        with path.open("r", newline="", encoding="utf-8") as csv_file:
            return [
                Candle(
                    open_time=datetime.fromisoformat(row["open_time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                for row in csv.DictReader(csv_file)
            ]

    def get_history(self, symbol, interval, start_date, end_date):
        self._validate_interval(interval)
        path = self._cache_path(symbol, interval)
        candles = self.load_csv(symbol, interval) if path.exists() else []
        if not candles:
            candles = self.download(symbol, interval, start_date, end_date)
            self.save_csv(candles, symbol, interval)
        return self._filter_range(candles, start_date, end_date)

    def _cache_path(self, symbol, interval):
        safe_symbol = symbol.upper().replace("/", "_")
        return self.storage_dir / f"{safe_symbol}_{interval}.csv"

    def _validate_interval(self, interval):
        if interval not in self.SUPPORTED_INTERVALS:
            supported = ", ".join(sorted(self.SUPPORTED_INTERVALS))
            raise ValueError(f"Unsupported interval '{interval}'. Use one of: {supported}")

    @staticmethod
    def _to_candle(kline):
        return Candle(
            open_time=datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc).replace(tzinfo=None),
            open=float(kline[1]),
            high=float(kline[2]),
            low=float(kline[3]),
            close=float(kline[4]),
            volume=float(kline[5]),
        )

    @staticmethod
    def _to_datetime(value, end_of_day=False):
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, date):
            result = datetime.combine(value, time.max if end_of_day else time.min)
        elif isinstance(value, str):
            if len(value) == 10:
                parsed_date = date.fromisoformat(value)
                result = datetime.combine(parsed_date, time.max if end_of_day else time.min)
            else:
                result = datetime.fromisoformat(value)
        else:
            raise TypeError("Dates must be datetime, date, or ISO-formatted strings")
        return result.replace(tzinfo=None)

    def _filter_range(self, candles, start_date, end_date):
        start = self._to_datetime(start_date)
        end = self._to_datetime(end_date, end_of_day=True)
        return [candle for candle in candles if start <= candle.open_time <= end]
