from app.paper.models import PaperExperiment
from app.paper.storage import PaperStorage


INITIAL_EXPERIMENTS = [
    ("BTCUSDT", "1h", "EMAStrategy", {"fast": 10, "slow": 50}),
    ("BTCUSDT", "1h", "EMARSIStrategy", {"fast": 20, "slow": 50, "rsi_period": 7, "oversold": 30, "overbought": 70}),
    ("BTCUSDT", "1h", "EMARSIATRStrategy", {"fast": 20, "slow": 50, "rsi_period": 7, "oversold": 30, "overbought": 70, "atr_period": 14, "stop_atr_multiplier": 2.0, "take_profit_atr_multiplier": 3.0}),
    ("BTCUSDT", "4h", "EMARSIStrategy", {"fast": 10, "slow": 50, "rsi_period": 7, "oversold": 30, "overbought": 70}),
    ("ETHUSDT", "1h", "EMARSIStrategy", {"fast": 10, "slow": 50, "rsi_period": 7, "oversold": 30, "overbought": 70}),
    ("ETHUSDT", "1h", "EMARSIATRStrategy", {"fast": 10, "slow": 50, "rsi_period": 7, "oversold": 30, "overbought": 70, "atr_period": 14, "stop_atr_multiplier": 2.0, "take_profit_atr_multiplier": 3.0}),
]


def create_initial_experiments(storage=None):
    storage = storage or PaperStorage()
    return [storage.create_experiment(PaperExperiment(symbol, timeframe, strategy, parameters))
            for symbol, timeframe, strategy, parameters in INITIAL_EXPERIMENTS]
