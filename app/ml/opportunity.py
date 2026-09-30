"""Research-only cross-asset opportunity ranking primitives."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

TARGET = "future_net_return_4h"
SEED = 42
DEFAULT_PARAMETERS = {"learning_rate": .05, "max_iter": 80, "max_leaf_nodes": 15,
                      "min_samples_leaf": 100, "l2_regularization": 1.0,
                      "early_stopping": False, "random_state": SEED}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value):
    if isinstance(value, dict): return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list): return [clean(item) for item in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating, float)): return float(value) if math.isfinite(value) else None
    return value


class OpportunityRanker:
    family = "sklearn_hist_gradient_boosting_regressor"

    def __init__(self, features, parameters=None, medians=None):
        self.features = list(features)
        self.parameters = dict(DEFAULT_PARAMETERS | dict(parameters or {}))
        self.medians = dict(medians or {})
        self.estimator = HistGradientBoostingRegressor(**self.parameters)

    def _matrix(self, frame, fit=False):
        missing = set(self.features) - set(frame.columns)
        if missing: raise ValueError(f"Feature mismatch: {sorted(missing)}")
        values = frame[self.features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
        if fit:
            self.medians = {name: float(values[name].median()) for name in self.features}
        return values.fillna(self.medians).to_numpy(dtype=float)

    def fit(self, frame):
        if set(frame.get("split", [])) - {"train"}:
            raise ValueError("Ranker fitting accepts train rows only")
        self.estimator.fit(self._matrix(frame, fit=True), frame[TARGET].to_numpy(dtype=float))
        return self

    def predict(self, frame):
        return self.estimator.predict(self._matrix(frame))

    def save(self, path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"features": self.features, "parameters": self.parameters,
                     "medians": self.medians, "estimator": self.estimator}, path)
        return path

    @classmethod
    def load(cls, path):
        payload = joblib.load(path); result = cls(payload["features"], payload["parameters"], payload["medians"])
        result.estimator = payload["estimator"]
        return result


def symbol_features(frame):
    result = frame.copy()
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        result[f"symbol_{symbol}"] = (result["symbol"] == symbol).astype(float)
    return result


def feature_sets(metadata):
    values = metadata["features"]
    return {"market_only": list(values["market"]),
            "market_cross_asset": list(values["market"] + values["cross_asset"]),
            "full": list(values["all"])}


def assert_feature_separation(features, metadata):
    forbidden = set(metadata["schema"]["target_columns"] + metadata["schema"]["metadata_columns"])
    forbidden |= {name for name in features if name.startswith("future_")}
    leaked = set(features) & forbidden
    if leaked: raise ValueError(f"Future/metadata fields in training features: {sorted(leaked)}")


def regression_metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return {"mae": float(mean_absolute_error(actual, predicted)),
            "rmse": float(mean_squared_error(actual, predicted) ** .5),
            "r2": float(r2_score(actual, predicted)),
            "pearson": float(pearsonr(actual, predicted).statistic),
            "spearman": float(spearmanr(actual, predicted).statistic), "rows": len(actual)}


def ranked_predictions(frame, predictions):
    result = frame.copy(); result["predicted_net_return"] = np.asarray(predictions, float)
    result["predicted_rank"] = result.groupby("timestamp")["predicted_net_return"].rank(method="first", ascending=False).astype(int)
    best = result[result["predicted_rank"] == 1][["timestamp", "symbol", "predicted_net_return"]].rename(
        columns={"symbol": "predicted_best_symbol", "predicted_net_return": "predicted_best_return"})
    return result.merge(best, on="timestamp", how="left", validate="many_to_one")


def ranking_metrics(ranked):
    winners = ranked[ranked["predicted_rank"] == 1]
    correlations = ranked.groupby("timestamp").apply(
        lambda group: (group["predicted_net_return"].corr(group[TARGET], method="spearman")
                       if group["predicted_net_return"].nunique() > 1 and group[TARGET].nunique() > 1 else np.nan),
        include_groups=False).dropna()
    actual_rank = winners["future_return_rank"].to_numpy(float)
    return {"top_1_accuracy": float((actual_rank == 1).mean()), "top_2_accuracy": float((actual_rank <= 2).mean()),
            "mean_actual_rank_of_predicted_winner": float(actual_rank.mean()),
            "mean_reciprocal_rank": float((1 / actual_rank).mean()),
            "average_spearman": float(correlations.mean()), "median_spearman": float(correlations.median()),
            "winner_positive_rate": float((winners[TARGET] > 0).mean()),
            "winner_profitable_rate": float(winners["is_positive_after_costs"].mean()),
            "timestamps": int(ranked["timestamp"].nunique())}


def simulate(ranked, threshold=-np.inf, selector="model", starting_balance=1000., exposure=.10):
    """One active four-hour theoretical position; net target already contains costs."""
    work = ranked.copy(); work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True); work["target_timestamp"] = pd.to_datetime(work["target_timestamp"], utc=True)
    equity = peak = starting_balance; available = pd.Timestamp.min.tz_localize("UTC"); ledger=[]; curve=[equity]
    first, last = work["timestamp"].min(), work["timestamp"].max()
    for timestamp, group in work.sort_values("timestamp").groupby("timestamp", sort=True):
        if timestamp < available: continue
        if selector == "model": row = group.loc[group["predicted_net_return"].idxmax()]; score = float(row["predicted_net_return"])
        elif selector == "btc":
            candidate = group[group["symbol"] == "BTCUSDT"]
            if candidate.empty: continue
            row = candidate.iloc[0]; score = np.inf
        elif selector == "momentum": row = group.loc[group["momentum_4_1h"].idxmax()]; score = np.inf
        elif selector == "oracle": row = group.loc[group[TARGET].idxmax()]; score = np.inf
        else: raise ValueError(selector)
        if score < threshold: continue
        notional = equity * exposure; pnl = notional * float(row[TARGET]); equity += pnl; peak=max(peak,equity); curve.append(equity)
        ledger.append({"timestamp": timestamp, "target_timestamp": row["target_timestamp"], "symbol": row["symbol"],
                       "predicted_return": None if selector != "model" else score, "actual_net_return": float(row[TARGET]),
                       "pnl": pnl, "equity": equity, "winner_actual_rank": int(row["future_return_rank"])})
        available = row["target_timestamp"]
    wins=[x["pnl"] for x in ledger if x["pnl"]>0]; losses=[x["pnl"] for x in ledger if x["pnl"]<=0]
    drawdown=min((value/max(curve[:i+1])-1 for i,value in enumerate(curve)), default=0.)
    hours=max(1.,(last-first).total_seconds()/3600); occupied=len(ledger)*4
    metrics={"net_return": equity/starting_balance-1,"pnl":equity-starting_balance,"trades":len(ledger),
             "win_rate":len(wins)/len(ledger) if ledger else 0.,"profit_factor":sum(wins)/abs(sum(losses)) if losses and sum(losses) else (float("inf") if wins else 0.),
             "expectancy":float(np.mean([x["pnl"] for x in ledger])) if ledger else 0.,"max_drawdown":abs(float(drawdown)),
             "fees":sum((x["equity"]-x["pnl"])*exposure*.0008 for x in ledger),
             "turnover":sum((x["equity"]-x["pnl"])*exposure*2 for x in ledger),
             "time_in_market":min(1.,occupied/hours),"ending_equity":equity,
             "rule":"one active position at a time; next decision allowed at target_timestamp; 10% equity exposure"}
    return clean(metrics), pd.DataFrame(ledger)


def select_threshold(validation_ranked, minimum_trades=100):
    best = validation_ranked[validation_ranked["predicted_rank"] == 1]["predicted_net_return"]
    grid = sorted(set([float("-inf")] + [float(best.quantile(q)) for q in np.linspace(.05,.95,19)] + [0.0]))
    candidates=[]
    for threshold in grid:
        metrics,_=simulate(validation_ranked,threshold)
        eligible=metrics["trades"]>=minimum_trades
        score=metrics["net_return"]-.5*metrics["max_drawdown"] if eligible else -1e9+metrics["trades"]
        candidates.append({"threshold":threshold,"eligible":eligible,"score":score,"trading":metrics})
    return max(candidates,key=lambda x:(x["score"],x["trading"]["trades"])),candidates


def selection_distribution(ledger, total_slots):
    counts=ledger["symbol"].value_counts() if not ledger.empty else pd.Series(dtype=int)
    rows=[{"selection":symbol,"count":int(counts.get(symbol,0)),"percentage":float(counts.get(symbol,0)/total_slots)} for symbol in ("BTCUSDT","ETHUSDT","SOLUSDT")]
    rows.append({"selection":"NO_TRADE","count":int(total_slots-len(ledger)),"percentage":float((total_slots-len(ledger))/total_slots)})
    return pd.DataFrame(rows)


def decision_distribution(ranked, threshold):
    """Distribution of raw hourly BEST_ASSET/NO_TRADE decisions before holding-period suppression."""
    winners = ranked[ranked["predicted_rank"] == 1]
    selected = winners[winners["predicted_net_return"] >= threshold]
    counts = selected["symbol"].value_counts()
    total = len(winners)
    rows = [{"selection": symbol, "count": int(counts.get(symbol, 0)),
             "percentage": float(counts.get(symbol, 0) / total)}
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    rows.append({"selection": "NO_TRADE", "count": int(total - len(selected)),
                 "percentage": float((total - len(selected)) / total)})
    return pd.DataFrame(rows)


def per_symbol_results(ledger):
    rows=[]
    for symbol in ("BTCUSDT","ETHUSDT","SOLUSDT"):
        group=ledger[ledger["symbol"]==symbol] if not ledger.empty else ledger
        rows.append({"symbol":symbol,"selection_count":len(group),"win_rate":float((group["actual_net_return"]>0).mean()) if len(group) else 0.,
                     "average_return":float(group["actual_net_return"].mean()) if len(group) else 0.,"pnl_contribution":float(group["pnl"].sum()) if len(group) else 0.})
    return pd.DataFrame(rows)


def grouped_walk_forward(frame, features, parameters, folds=3):
    timestamps=np.array(sorted(pd.to_datetime(frame["timestamp"],utc=True).unique())); n=len(timestamps); rows=[]
    boundaries=[(.40,.50,.667),(.50,.667,.834),(.667,.834,1.0)]
    for number,(train_end,val_end,test_end) in enumerate(boundaries[:folds],1):
        a,b,c=timestamps[int(n*train_end)],timestamps[int(n*val_end)],timestamps[min(n-1,int(n*test_end)-1)]
        stamp=pd.to_datetime(frame["timestamp"],utc=True); target=pd.to_datetime(frame["target_timestamp"],utc=True)
        train=frame[(stamp<a)&(target<a)].copy(); train["split"]="train"
        validation=frame[(stamp>=a)&(stamp<b)&(target<b)].copy(); evaluation=frame[(stamp>=b)&(stamp<=c)].copy()
        model=OpportunityRanker(features,parameters).fit(train)
        validation_ranked=ranked_predictions(validation,model.predict(validation)); selected,_=select_threshold(validation_ranked,min(100,max(20,validation["timestamp"].nunique()//30)))
        ranked=ranked_predictions(evaluation,model.predict(evaluation)); trading,_=simulate(ranked,selected["threshold"]); ranking=ranking_metrics(ranked)
        rows.append({"fold":number,"train_end":str(a),"validation_start":str(a),"validation_end":str(b),"evaluation_start":str(b),"evaluation_end":str(c),
                     "threshold":selected["threshold"],"top_1_accuracy":ranking["top_1_accuracy"],"rank_correlation":ranking["average_spearman"],
                     "net_return":trading["net_return"],"trades":trading["trades"],"profit_factor":trading["profit_factor"],"max_drawdown":trading["max_drawdown"]})
    return rows
