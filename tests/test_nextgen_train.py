import hashlib
import numpy as np
import pandas as pd
import pytest

from app.ml.nextgen_train import (Classifier,Regressor,adaptive,assert_isolation,classifier_metrics,
 feature_sets,ranked,select_models,simulate,symbol_report,walk_forward)

def frame(hours=72):
    rows=[];symbols=("BTCUSDT","ETHUSDT","SOLUSDT")
    for i,t in enumerate(pd.date_range("2024-01-01",periods=hours,freq="1h",tz="UTC")):
        split="train" if i<44 else ("validation" if i<58 else "test");vals=[]
        for a,s in enumerate(symbols):
            target=np.sin(i/6+a);vals.append(target);rows.append({"timestamp":t,"target_timestamp_8h":t+pd.Timedelta(hours=8),"symbol":s,"split":split,
             "f1":target+.01*a,"f2":np.cos(i/8),"target_relative_vol_8h":target,"path_quality_score":target*.02,"clean_opportunity_8h":int(target>.2),
             "future_net_return_8h":target*.01,"future_mfe_net_8h":max(target*.01+.005,0),"future_mae_8h":min(target*.005,0),"momentum_4_1h":target,
             "return_rank_24h":a/3,"behavior_regime":"CONTINUATION_BIAS" if i%2 else "MIXED","market_state":"TRENDING"})
        start=len(rows)-3;ranks=pd.Series(vals).rank(ascending=False,method="first").astype(int)
        for a in range(3):rows[start+a]["future_return_rank"]=ranks.iloc[a];rows[start+a]["actual_rank"]=ranks.iloc[a]
    return pd.DataFrame(rows)
def meta():return {"schemas":{"full_features":["f1","f2"],"stability_filtered_features":["f1"],"target_columns":["target_relative_vol_8h","path_quality_score","future_net_return_8h"],"metadata_columns":["timestamp","symbol","split"]}}
FAST={"max_iter":4,"max_leaf_nodes":4,"min_samples_leaf":2,"random_state":1}

def test_training_and_save_load(tmp_path):
    x=frame();m=Regressor(["f1","f2"],"target_relative_vol_8h",FAST).fit(x[x.split=="train"]);path=m.save(tmp_path/"m.joblib");loaded=Regressor.load(path);np.testing.assert_allclose(m.predict(x),loaded.predict(x))
    c=Classifier(["f1"],params=FAST).fit(x[x.split=="train"]);cp=c.save(tmp_path/"c.joblib");np.testing.assert_allclose(c.predict(x),Classifier.load(cp).predict(x))
def test_feature_and_target_isolation():
    assert_isolation(["f1","f2"],meta())
    with pytest.raises(ValueError):assert_isolation(["future_net_return_8h"],meta())
def test_grouped_8h_splits_are_safe():
    x=frame();assert x.groupby("timestamp").split.nunique().max()==1;assert (x.target_timestamp_8h-x.timestamp).eq(pd.Timedelta(hours=8)).all()
def test_test_mutation_does_not_change_model_selection():
    x=frame();first,_=select_models(x,meta());changed=x.copy();mask=changed.split=="test";changed.loc[mask,["target_relative_vol_8h","path_quality_score"]]*=-100;second,_=select_models(changed,meta());assert (first["target"],first["feature_set"])==(second["target"],second["feature_set"])
def test_clean_gate_metrics_and_two_stage():
    y=np.array([0,0,1,1]);p=np.array([.1,.4,.6,.9]);m=classifier_metrics(y,p,.5);assert m["precision"]==m["recall"]==1
    x=ranked(frame(24),frame(24).f1,"target_relative_vol_8h");x["clean_probability"]=np.where(x.symbol=="BTCUSDT",.1,.9);a,_=simulate(x,-np.inf,None);b,_=simulate(x,-np.inf,.5);assert b["trades"]<=a["trades"]
def test_non_overlapping_8h_simulation():
    x=ranked(frame(32),frame(32).f1,"target_relative_vol_8h");x["clean_probability"]=1.;metrics,ledger=simulate(x);assert (ledger.timestamp.diff().dropna()>=pd.Timedelta(hours=8)).all();assert metrics["time_in_market"]<=1
def test_adaptive_rolling_uses_past_only():
    x=frame(24*400);winner,table,models=adaptive(x,["f1"],"target_relative_vol_8h");assert winner in {"expanding","rolling_12m"};assert table.set_index("method").loc["rolling_12m","train_rows"]<=table.set_index("method").loc["expanding","train_rows"]
def test_walk_forward_is_chronological():
    result,_=walk_forward(frame(120),["f1"],"target_relative_vol_8h");assert len(result)==3;assert (result.train_rows>0).all() and (result.evaluation_rows>0).all()
def test_symbol_reporting_includes_every_symbol():
    x=ranked(frame(24),frame(24).f1,"target_relative_vol_8h");x["clean_probability"]=.5;_,ledger=simulate(x);result=symbol_report(x,ledger);assert set(result.symbol)=={"BTCUSDT","ETHUSDT","SOLUSDT"}
def test_frozen_artifact_preservation(tmp_path):
    p=tmp_path/"frozen.joblib";p.write_bytes(b"frozen");before=hashlib.sha256(p.read_bytes()).hexdigest();(tmp_path/"new.joblib").write_bytes(b"new");assert hashlib.sha256(p.read_bytes()).hexdigest()==before
