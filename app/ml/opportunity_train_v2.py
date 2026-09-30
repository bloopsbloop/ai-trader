"""Train Opportunity Ranker v2 using cross-asset-v2 and validation-only selection."""
from __future__ import annotations

import argparse,json
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd

from app.ml.cross_asset_dataset import PROJECT_ROOT
from app.ml.opportunity import (OpportunityRanker,assert_feature_separation,clean,feature_sets,file_hash,
 ranked_predictions,ranking_metrics,regression_metrics,select_threshold)

DEFAULT_DATASET=PROJECT_ROOT/"data/ml/cross_asset_v2/cross_asset_v2.csv.gz"
DEFAULT_METADATA=PROJECT_ROOT/"data/ml/cross_asset_v2/metadata.json"
DEFAULT_REGISTRY=PROJECT_ROOT/"data/models/opportunity_ranker_v2"
PARAMETERS={"learning_rate":.05,"max_iter":50,"max_leaf_nodes":15,"min_samples_leaf":150,"l2_regularization":1.,"early_stopping":False,"random_state":42}
TARGETS={"raw_return":"future_net_return_4h","risk_adjusted":"risk_adjusted_target","relative_opportunity":"relative_opportunity_target"}


def load_v2(dataset=DEFAULT_DATASET,metadata_path=DEFAULT_METADATA):
    meta=json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if meta["dataset_version"]!="cross-asset-v2":raise ValueError("Ranker v2 requires cross-asset-v2")
    frame=pd.read_csv(dataset,parse_dates=["timestamp","target_timestamp"])
    frame["relative_opportunity_target"]=frame["future_net_return_4h"]-frame.groupby("timestamp")["future_net_return_4h"].transform("median")
    return frame,meta


def select_configuration(frame,metadata,parameters=PARAMETERS,minimum_trades=100):
    sets=feature_sets(metadata);train=frame[frame.split=="train"].copy();validation=frame[frame.split=="validation"].copy();candidates=[]
    for target_label,target_column in TARGETS.items():
        for feature_label,features in sets.items():
            assert_feature_separation(features,metadata)
            model=OpportunityRanker(features,parameters,target_name=target_column).fit(train)
            train_prediction=model.predict(train);validation_prediction=model.predict(validation)
            ranked=ranked_predictions(validation,validation_prediction);ranking=ranking_metrics(ranked);selected,thresholds=select_threshold(ranked,minimum_trades)
            candidates.append({"target_label":target_label,"target_column":target_column,"feature_set":feature_label,"features":features,"model":model,
             "train_regression":regression_metrics(train[target_column],train_prediction),"validation_regression":regression_metrics(validation[target_column],validation_prediction),
             "validation_ranking":ranking,"threshold":selected["threshold"],"threshold_selection":clean(selected),"threshold_candidates":clean(thresholds)})
    winner=max(candidates,key=lambda x:(x["validation_ranking"]["top_1_accuracy"],x["validation_ranking"]["average_spearman"],x["validation_ranking"]["ndcg_at_3"],x["threshold_selection"]["score"]))
    return winner,candidates


def train_and_save(dataset=DEFAULT_DATASET,metadata_path=DEFAULT_METADATA,registry=DEFAULT_REGISTRY,force=False,parameters=PARAMETERS):
    registry=Path(registry)
    if registry.exists() and any(registry.iterdir()) and not force:raise FileExistsError(registry)
    registry.mkdir(parents=True,exist_ok=True);frame,meta=load_v2(dataset,metadata_path);winner,candidates=select_configuration(frame,meta,parameters)
    model_path=winner["model"].save(registry/"model.joblib")
    (registry/"features.json").write_text(json.dumps(winner["features"],indent=2),encoding="utf-8")
    (registry/"target.json").write_text(json.dumps({"label":winner["target_label"],"column":winner["target_column"],"definition":"target-only; never a feature"},indent=2),encoding="utf-8")
    candidate_dir=registry/"candidates";candidate_dir.mkdir(exist_ok=True);comparison=[]
    for row in candidates:
        candidate_path=row["model"].save(candidate_dir/f"{row['target_label']}__{row['feature_set']}.joblib")
        exported={k:v for k,v in row.items() if k not in {"model","features","threshold_candidates"}}
        exported["model_file"]=str(candidate_path.relative_to(registry));exported["model_sha256"]=file_hash(candidate_path);comparison.append(exported)
    result={"model_version":"opportunity_ranker_v2","model_family":OpportunityRanker.family,"created_at":datetime.now(timezone.utc).isoformat(),
     "research_only":True,"not_for_batch3_control":True,"dataset_version":"cross-asset-v2","dataset_sha256":file_hash(dataset),
     "target_label":winner["target_label"],"target_column":winner["target_column"],"feature_set":winner["feature_set"],"features":winner["features"],
     "feature_count":len(winner["features"]),"parameters":winner["model"].parameters,"decision_threshold":winner["threshold"],
     "threshold_selected_on":"cross-asset-v2 Validation only","minimum_validation_trades":100,"periods":meta["periods"],
     "validation_ranking":winner["validation_ranking"],"train_regression":winner["train_regression"],"validation_regression":winner["validation_regression"],
     "candidate_comparison":comparison,"test_evaluated_during_training":False,"model_sha256":file_hash(model_path)}
    (registry/"metadata.json").write_text(json.dumps(clean(result),indent=2),encoding="utf-8");return result


def main(argv=None):
    p=argparse.ArgumentParser(description="Train research-only Opportunity Ranker v2");p.add_argument("--dataset",default=str(DEFAULT_DATASET));p.add_argument("--metadata",default=str(DEFAULT_METADATA));p.add_argument("--registry",default=str(DEFAULT_REGISTRY));p.add_argument("--force",action="store_true");a=p.parse_args(argv)
    r=train_and_save(a.dataset,a.metadata,a.registry,a.force);print(json.dumps({k:r[k] for k in ("model_version","target_label","feature_set","feature_count","parameters","decision_threshold","validation_ranking","model_sha256")},indent=2))


if __name__=="__main__":main()
