from app.paper.models import PaperExperiment
from app.paper.storage import PaperStorage
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import json


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


BATCH_2_CONFIGS = [
    ("BTCUSDT", "15m", "BollingerMeanReversionStrategy", {"period": 20, "std_multiplier": 2.0, "rsi_period": 14, "rsi_oversold": 30}),
    ("ETHUSDT", "15m", "BollingerMeanReversionStrategy", {"period": 20, "std_multiplier": 2.0}),
    ("SOLUSDT", "15m", "BollingerMeanReversionStrategy", {"period": 30, "std_multiplier": 2.5}),
    ("XRPUSDT", "1h", "BollingerMeanReversionStrategy", {"period": 15, "std_multiplier": 1.5}),
    ("LINKUSDT", "4h", "BollingerMeanReversionStrategy", {"period": 15, "std_multiplier": 1.5}),
    ("BTCUSDT", "15m", "DonchianBreakoutStrategy", {"entry_period": 10, "exit_period": 5}),
    ("SOLUSDT", "15m", "DonchianBreakoutStrategy", {"entry_period": 40, "exit_period": 20}),
    ("BNBUSDT", "1h", "DonchianBreakoutStrategy", {"entry_period": 20, "exit_period": 10}),
    ("LINKUSDT", "1h", "DonchianBreakoutStrategy", {"entry_period": 40, "exit_period": 10}),
    ("DOGEUSDT", "4h", "DonchianBreakoutStrategy", {"entry_period": 55, "exit_period": 20}),
    ("BTCUSDT", "15m", "MACDTrendStrategy", {"fast": 8, "slow": 21, "signal_period": 7}),
    ("ETHUSDT", "15m", "MACDTrendStrategy", {"fast": 8, "slow": 21, "signal_period": 7}),
    ("BNBUSDT", "1h", "MACDTrendStrategy", {"fast": 12, "slow": 26, "signal_period": 9}),
    ("XRPUSDT", "1h", "MACDTrendStrategy", {"fast": 8, "slow": 21, "signal_period": 7}),
    ("SOLUSDT", "4h", "MACDTrendStrategy", {"fast": 8, "slow": 21, "signal_period": 7}),
    ("ETHUSDT", "15m", "SupertrendStrategy", {"atr_period": 10, "multiplier": 3.0}),
    ("SOLUSDT", "15m", "SupertrendStrategy", {"atr_period": 7, "multiplier": 3.0}),
    ("DOGEUSDT", "15m", "SupertrendStrategy", {"atr_period": 14, "multiplier": 4.0}),
    ("BNBUSDT", "1h", "SupertrendStrategy", {"atr_period": 10, "multiplier": 3.0}),
    ("LINKUSDT", "4h", "SupertrendStrategy", {"atr_period": 7, "multiplier": 2.0}),
    ("BTCUSDT", "15m", "ADXEMATrendStrategy", {"fast": 10, "slow": 30, "adx_period": 10, "adx_threshold": 20}),
    ("ETHUSDT", "15m", "ADXEMATrendStrategy", {"fast": 10, "slow": 50, "adx_period": 14, "adx_threshold": 20}),
    ("SOLUSDT", "1h", "ADXEMATrendStrategy", {"fast": 10, "slow": 30, "adx_period": 10, "adx_threshold": 20}),
    ("XRPUSDT", "1h", "ADXEMATrendStrategy", {"fast": 10, "slow": 30, "adx_period": 10, "adx_threshold": 20}),
    ("BNBUSDT", "4h", "ADXEMATrendStrategy", {"fast": 20, "slow": 50, "adx_period": 14, "adx_threshold": 25}),
    ("BTCUSDT", "15m", "RSIMeanReversionStrategy", {"rsi_period": 7, "oversold": 25, "exit_level": 50, "trend_ema": 100}),
    ("XRPUSDT", "15m", "RSIMeanReversionStrategy", {"rsi_period": 14, "oversold": 30, "exit_level": 50}),
    ("DOGEUSDT", "15m", "RSIMeanReversionStrategy", {"rsi_period": 7, "oversold": 20, "exit_level": 45}),
    ("LINKUSDT", "1h", "RSIMeanReversionStrategy", {"rsi_period": 14, "oversold": 25, "exit_level": 55}),
    ("ETHUSDT", "4h", "RSIMeanReversionStrategy", {"rsi_period": 7, "oversold": 30, "exit_level": 45}),
]


def batch_2_experiments():
    result = []
    for symbol, timeframe, strategy, parameters in BATCH_2_CONFIGS:
        key = f"batch_2:{symbol}:{timeframe}:{strategy}:{json.dumps(parameters, sort_keys=True)}"
        slippage = 0.0005 if symbol in {"BTCUSDT", "ETHUSDT"} else (0.0015 if symbol == "DOGEUSDT" else 0.001)
        result.append(PaperExperiment(symbol, timeframe, strategy, parameters, slippage=slippage,
                                      position_size=0.15, max_position_fraction=0.15,
                                      experiment_id=str(uuid5(NAMESPACE_URL, key)), batch_id="batch_2",
                                      batch_name="Multi-Asset Strategy Lab",
                                      parameter_origin="Batch 2 compact diversified candidate configuration"))
    return result


def create_batch_2_experiments(storage=None, export_path=None, selection_path=None):
    storage = storage or PaperStorage()
    storage.backup("before_batch_2")
    selection_path = Path(selection_path or Path(__file__).resolve().parents[2] / "data" / "research" / "batch_2" / "selected_experiments.json")
    selected_ids = {row["experiment_id"] for row in json.loads(selection_path.read_text(encoding="utf-8"))} if selection_path.exists() else {experiment.experiment_id for experiment in batch_2_experiments()}
    experiments = [storage.create_experiment_if_missing(experiment) for experiment in batch_2_experiments() if experiment.experiment_id in selected_ids]
    export_path = Path(export_path or Path(__file__).resolve().parents[2] / "data" / "paper" / "batch_2_experiments.json")
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps([experiment.as_dict() for experiment in experiments], indent=2), encoding="utf-8")
    return experiments
