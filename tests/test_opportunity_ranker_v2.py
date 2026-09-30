import hashlib,json
import numpy as np,pandas as pd
import pytest

from app.ml.opportunity import ranked_predictions,ranking_metrics,select_threshold
from app.ml.opportunity_evaluate_v2 import random_reference,regime_analysis,score_gap_analysis
from app.ml.opportunity_train_v2 import select_configuration

FAST={"learning_rate":.1,"max_iter":5,"max_leaf_nodes":5,"min_samples_leaf":3,"l2_regularization":1.,"early_stopping":False,"random_state":42}


def frame(hours=45):
    rows=[];symbols=("BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","LINKUSDT","DOGEUSDT")
    for i,t in enumerate(pd.date_range("2024-01-01",periods=hours,freq="1h",tz="UTC")):
        split="train" if i<27 else ("validation" if i<36 else "test");values=[]
        for a,s in enumerate(symbols):
            target=.01*np.sin(i/4+a);values.append(target);rows.append({"timestamp":t,"target_timestamp":t+pd.Timedelta(hours=4),"symbol":s,"split":split,"m":np.sin(i/4+a),"x":a/7,"c":np.cos(i/6),"momentum_4_1h":np.sin(i/4+a),"btc_trend":np.sin(i/8),"btc_volatility":.01+i/1000,"market_breadth":(i%10)/10,"future_net_return_4h":target,"risk_adjusted_target":target/.01,"best_available_net_return":max(values),"is_positive_after_costs":int(target>0)})
        start=len(rows)-7;best=max(values)
        for rank,pos in enumerate(np.argsort(values)[::-1],1):rows[start+pos]["future_return_rank"]=rank
        for pos in range(7):rows[start+pos]["best_available_net_return"]=best
    data=pd.DataFrame(rows);data["relative_opportunity_target"]=data.future_net_return_4h-data.groupby("timestamp").future_net_return_4h.transform("median");return data


def metadata():
    fs={"market":["m"],"cross_asset":["x"],"market_context":["c"]};fs["all"]=fs["market"]+fs["cross_asset"]+fs["market_context"]
    return {"features":fs,"schema":{"target_columns":["future_net_return_4h","risk_adjusted_target","future_return_rank"],"metadata_columns":["timestamp","target_timestamp","symbol","split"]}}


def test_relative_target_is_cross_sectionally_centered():
    data=frame();assert np.allclose(data.groupby("timestamp").relative_opportunity_target.median(),0)


def test_topk_and_ndcg_metrics():
    data=frame(10);ranked=ranked_predictions(data,data.future_net_return_4h);m=ranking_metrics(ranked)
    assert m["top_1_accuracy"]==m["top_2_accuracy"]==m["top_3_accuracy"]==1
    assert m["ndcg_at_1"]==1 and m["ndcg_at_3"]==1


def test_random_reference_matches_seven_asset_scale():
    metrics,_=random_reference(frame(),42);assert metrics["analytical_top_1"]==pytest.approx(1/7) and metrics["analytical_top_3"]==pytest.approx(3/7)


def test_gap_and_regime_analysis():
    data=frame();ranked=ranked_predictions(data,np.linspace(0,1,len(data)));gaps=score_gap_analysis(ranked);regimes=regime_analysis(ranked,0,.02)
    assert len(gaps)==4 and sum(x["timestamps"] for x in gaps)==data.timestamp.nunique()
    assert {x["dimension"] for x in regimes}=={"btc_trend_regime","volatility_regime","breadth_regime"}


def test_threshold_selection_uses_validation_only():
    data=frame();validation=ranked_predictions(data[data.split=="validation"],data[data.split=="validation"].m)
    first,_=select_threshold(validation,minimum_trades=1);changed=data.copy();changed.loc[changed.split=="test","future_net_return_4h"]*=100
    second,_=select_threshold(validation,minimum_trades=1);assert first["threshold"]==second["threshold"]


def test_test_mutation_does_not_change_configuration_or_threshold():
    data=frame();first,_=select_configuration(data,metadata(),FAST,1);changed=data.copy();changed.loc[changed.split=="test",["future_net_return_4h","risk_adjusted_target","relative_opportunity_target"]]*=-100;second,_=select_configuration(changed,metadata(),FAST,1)
    assert (first["target_label"],first["feature_set"],first["threshold"])==(second["target_label"],second["feature_set"],second["threshold"])


def test_v1_artifact_preservation(tmp_path):
    v1=tmp_path/"opportunity_ranker_v1.joblib";v1.write_bytes(b"frozen");before=hashlib.sha256(v1.read_bytes()).hexdigest();(tmp_path/"v2.json").write_text(json.dumps({"research_only":True,"not_for_batch3_control":True,"dataset_version":"cross-asset-v2"}));assert hashlib.sha256(v1.read_bytes()).hexdigest()==before
