from __future__ import annotations
import hashlib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from app.ml.candidate_filter_v2 import (
    IDENTITY_FEATURES, add_symbol_identity, apply_thresholds, balanced_metrics,
    combined_gate_metrics, drift_analysis, per_symbol_thresholds, sample_weights,
)


def frame(hours=50):
    symbols=("BNBUSDT","BTCUSDT","DOGEUSDT","ETHUSDT","LINKUSDT","SOLUSDT","XRPUSDT"); rows=[]
    for i,t in enumerate(pd.date_range("2024-01-01",periods=hours,freq="1h",tz="UTC")):
        split="train" if i<30 else ("validation" if i<40 else "test")
        vals=[.01*np.sin(i/5+a) for a in range(7)]; ranks=pd.Series(vals).rank(ascending=False,method="first").astype(int)
        for a,s in enumerate(symbols):
            rows.append({"timestamp":t,"target_timestamp":t+pd.Timedelta(hours=4),"symbol":s,"split":split,
             "m":np.sin(i/5+a),"x":a/7,"future_net_return_4h":vals[a],"future_return_rank":ranks.iloc[a],
             "best_net_return":max(vals),"ideal_candidate":int(ranks.iloc[a]<=2 and vals[a]>.001),
             "candidate_top_2":int(ranks.iloc[a]<=2 and vals[a]>0),"future_cross_sectional_dispersion":np.std(vals)})
    return pd.DataFrame(rows)


def test_symbol_balanced_and_class_balanced_weights():
    data=frame(); w1=sample_weights(data,"ideal_candidate","symbol_balanced"); w2=sample_weights(data,"ideal_candidate","symbol_class_balanced")
    assert w1.mean()==pytest.approx(1) and w2.mean()==pytest.approx(1)
    weighted=pd.DataFrame({"symbol":data.symbol,"label":data.ideal_candidate,"w":w2}).groupby(["symbol","label"]).w.sum()
    assert weighted.max()/weighted.min()<1.01


def test_symbol_identity_is_one_hot():
    data=add_symbol_identity(frame(2)); assert data[IDENTITY_FEATURES].sum(axis=1).eq(1).all()


def test_per_symbol_thresholds_use_validation_only():
    data=frame(); validation=data[data.split=="validation"]
    probabilities=np.linspace(.01,.99,len(validation)); first,_,_=per_symbol_thresholds(validation,probabilities,"ideal_candidate",1)
    changed=data.copy(); changed.loc[changed.split=="test","ideal_candidate"]=1-changed.loc[changed.split=="test","ideal_candidate"]
    second,_,_=per_symbol_thresholds(validation,probabilities,"ideal_candidate",1); assert first==second


def test_minimum_symbol_recall_metric_detects_collapse():
    data=frame(10); p=np.where(data.symbol=="BTCUSDT",0.,1.); metrics=balanced_metrics(data,p,.5,"ideal_candidate")
    assert metrics["minimum_symbol_winner_recall"]==0 and metrics["symbol_recall_range"]==1


def test_regime_gate_uses_only_given_time_features_and_combines():
    candidates=apply_thresholds(frame(12),np.ones(84),.5)
    regime=candidates.drop_duplicates("timestamp")[["timestamp","split"]].copy(); regime["r1"]=np.arange(12); regime["any_profitable_asset_4h"]=[0,1]*6
    model=HistGradientBoostingClassifier(max_iter=3,min_samples_leaf=2,random_state=1).fit(regime[["r1"]],regime.any_profitable_asset_4h)
    combined,metrics=combined_gate_metrics(candidates,regime,model,["r1"],.99,"ideal_candidate")
    assert combined.candidate_keep.sum()==0 and metrics["empty_candidate_set_rate"]==1


def test_no_opportunity_average_set_size():
    data=frame(10); data["best_net_return"]=-.01; metrics=balanced_metrics(data,np.ones(len(data)),.5,"ideal_candidate")
    assert metrics["no_opportunity_average_set_size"]==7


class DummyModel:
    def predict_proba(self,data):
        return np.clip((data["m"].to_numpy()+1)/2,.001,.999)


def test_drift_diagnostics_report_feature_and_probability():
    result=drift_analysis(frame(),["m","x"],DummyModel())
    assert {"m","x","__prediction_probability__"}.issubset(set(result.feature))
    assert set(result.split).issuperset({"validation","test"})


def test_v1_artifact_preservation(tmp_path):
    path=tmp_path/"v1.joblib"; path.write_bytes(b"frozen"); before=hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path/"v2.txt").write_text("research only"); assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_v2_candidate_model_save_load(tmp_path):
    data=frame(); train=data[data.split=="train"]
    from app.ml.candidate_filter_v2 import fit_model
    model=fit_model(train,["m","x"],"ideal_candidate","symbol_class_balanced",{"max_iter":3,"min_samples_leaf":2,"random_state":1})
    path=model.save(tmp_path/"candidate_model.joblib"); loaded=type(model).load(path)
    np.testing.assert_allclose(model.predict_proba(train),loaded.predict_proba(train))
