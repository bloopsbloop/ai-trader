from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.historical_data import HistoricalDataProvider


class FakeBinanceClient:
    def __init__(self):
        self.calls = []

    def get_historical_klines(self, **kwargs):
        self.calls.append(kwargs)
        return [
            [1704067200000, "42000", "42500", "41500", "42300", "100.5"],
            [1704070800000, "42300", "43000", "42000", "42800", "101.0"],
        ]


class HistoricalDataProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.client = FakeBinanceClient()
        self.provider = HistoricalDataProvider(
            client=self.client,
            storage_dir=Path(self.temporary_directory.name),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_download_creates_candle_models(self):
        candles = self.provider.download("btcusdt", "1h", "2024-01-01", "2024-01-01")

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open_time, datetime(2024, 1, 1))
        self.assertEqual(candles[0].close, 42300.0)
        self.assertEqual(self.client.calls[0]["symbol"], "BTCUSDT")

    def test_get_history_reads_existing_cache_without_downloading(self):
        downloaded = self.provider.get_history("BTCUSDT", "1h", "2024-01-01", "2024-01-01")
        cached = self.provider.get_history("BTCUSDT", "1h", "2024-01-01", "2024-01-01")

        self.assertEqual(len(downloaded), 2)
        self.assertEqual(cached, downloaded)
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue((Path(self.temporary_directory.name) / "BTCUSDT_1h.csv").exists())

    def test_rejects_unsupported_interval(self):
        with self.assertRaises(ValueError):
            self.provider.get_history("BTCUSDT", "2h", "2024-01-01", "2024-01-01")


if __name__ == "__main__":
    unittest.main()
