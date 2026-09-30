import json

import numpy as np
import pandas as pd
import pytest

from app.ml.opportunity import (OpportunityRanker, assert_feature_separation, feature_sets,
                                ranked_predictions, ranking_metrics, select_threshold,
                                selection_distribution, simulate)
from app.ml.opportunity_train import train_and_save, train_candidate


SMALL = {"max_iter": 8, "max_leaf_nodes": 7, "min_samples_leaf": 5,
         "learning_rate": .1, "l2_regularization": 1., "early_stopping": False,
         "random_state": 42}


def dataset(hours=90):
    rows=[]
    times=pd.date_range("2024-01-01",periods=hours,freq="1h",tz="UTC")
    for index,timestamp in enumerate(times):
        split="train" if index<54 else ("validation" if index<72 else "test")
        values=[]
        for asset,symbol in enumerate(("BTCUSDT","ETHUSDT","SOLUSDT")):
            target=.004*np.sin(index/5+asset)-.001*asset
            values.append(target)
            rows.append({"timestamp":timestamp,"target_timestamp":timestamp+pd.Timedelta(hours=4),"source_open_time":timestamp-pd.Timedelta(hours=1),
                         "source_timestamp_15m":timestamp,"source_timestamp_1h":timestamp,"source_timestamp_4h":timestamp.floor("4h"),
                         "symbol":symbol,"split":split,"eligible_universe_size":3,"close":100+index+asset,
                         "f_market":np.sin(index/5+asset),"f_cross":asset/2,"f_context":np.cos(index/7),
                         "momentum_4_1h":np.sin(index/5+asset),"future_net_return_4h":target,
                         "best_available_net_return":0.0,"is_positive_after_costs":int(target>0)})
        start=len(rows)-3
        order=np.argsort(values)[::-1]
        for rank,position in enumerate(order,1): rows[start+position]["future_return_rank"]=rank
        best=max(values)
        for position in range(3): rows[start+position]["best_available_net_return"]=best
    return pd.DataFrame(rows)


def metadata():
    features={"market":["f_market"],"cross_asset":["f_cross"],"market_context":["f_context"]}
    features["all"]=features["market"]+features["cross_asset"]+features["market_context"]
    return {"dataset_version":"cross-asset-v1","features":features,
            "schema":{"target_columns":["future_net_return_4h","future_return_rank","is_positive_after_costs","best_available_net_return"],
                      "metadata_columns":["timestamp","target_timestamp","symbol","split","eligible_universe_size","source_open_time","source_timestamp_15m","source_timestamp_1h","source_timestamp_4h","close"]},
            "periods":{"train":{},"validation":{},"test":{}}}


def test_feature_target_separation_and_ablation_sets():
    sets=feature_sets(metadata()); assert [len(sets[x]) for x in ("market_only","market_cross_asset","full")]==[1,2,3]
    assert_feature_separation(sets["full"],metadata())
    with pytest.raises(ValueError): assert_feature_separation(sets["full"]+["future_net_return_4h"],metadata())


def test_group_safe_training_and_fit_guard():
    data=dataset(); assert data.groupby("timestamp")["split"].nunique().max()==1
    with pytest.raises(ValueError): OpportunityRanker(["f_market"],SMALL).fit(data[data.split!="test"])


def test_ranking_and_top1_metrics():
    data=dataset(10); predictions=data["future_net_return_4h"].to_numpy()
    ranked=ranked_predictions(data,predictions)
    assert ranked.groupby("timestamp")["predicted_rank"].min().eq(1).all()
    assert ranking_metrics(ranked)["top_1_accuracy"]==1


def test_validation_threshold_and_test_label_isolation():
    data=dataset(); selected,_=train_candidate(data,metadata(),SMALL)
    changed=data.copy(); changed.loc[changed.split=="test","future_net_return_4h"]*= -100
    repeated,_=train_candidate(changed,metadata(),SMALL)
    assert selected["threshold"]==repeated["threshold"]
    assert selected["include_symbol_identity"]==repeated["include_symbol_identity"]
    validation=data[data.split=="validation"]
    view=validation.copy()
    if selected["include_symbol_identity"]:
        from app.ml.opportunity import symbol_features
        view=symbol_features(view)
    np.testing.assert_allclose(selected["model"].predict(view),repeated["model"].predict(view))


def test_non_overlapping_simulation_and_no_trade():
    data=dataset(20); ranked=ranked_predictions(data,data.future_net_return_4h)
    traded,ledger=simulate(ranked,-np.inf)
    assert traded["trades"]<=5
    assert (pd.to_datetime(ledger.timestamp.iloc[1:].to_numpy(),utc=True)>=pd.to_datetime(ledger.target_timestamp.iloc[:-1].to_numpy(),utc=True)).all()
    none,empty=simulate(ranked,float("inf")); assert none["trades"]==0 and empty.empty


def test_selection_distribution_includes_no_trade():
    ranked=ranked_predictions(dataset(20),dataset(20).future_net_return_4h)
    _,ledger=simulate(ranked,-np.inf); result=selection_distribution(ledger,20,("BTCUSDT","ETHUSDT","SOLUSDT"))
    assert set(result.selection)=={"BTCUSDT","ETHUSDT","SOLUSDT","NO_TRADE"}
    assert result["count"].sum()==20


def test_model_save_load_and_research_registry(tmp_path):
    data=dataset(); model=OpportunityRanker(["f_market"],SMALL).fit(data[data.split=="train"])
    path=model.save(tmp_path/"model.joblib"); loaded=OpportunityRanker.load(path)
    np.testing.assert_allclose(model.predict(data.iloc[:10]),loaded.predict(data.iloc[:10]))
    csv=tmp_path/"dataset.csv.gz"; meta=tmp_path/"metadata.json"; registry=tmp_path/"registry"
    data.to_csv(csv,index=False,compression="gzip"); meta.write_text(json.dumps(metadata()),encoding="utf-8")
    saved=train_and_save(csv,meta,registry,parameters=SMALL)
    assert saved["research_only"] is True and saved["not_for_batch3_control"] is True
    assert saved["universe_size_limitations"] is True and saved["test_evaluated_during_training"] is False
