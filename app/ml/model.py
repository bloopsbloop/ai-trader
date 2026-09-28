"""Reusable decision-model abstraction and portable ML v1 implementation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression


class BaseDecisionModel(ABC):
    @abstractmethod
    def fit(self, features, labels, sample_weight=None): ...

    @abstractmethod
    def predict(self, features): ...

    @abstractmethod
    def predict_proba(self, features): ...

    @abstractmethod
    def save(self, path): ...

    @classmethod
    @abstractmethod
    def load(cls, path): ...

    @abstractmethod
    def feature_names(self): ...

    @abstractmethod
    def metadata(self): ...


@dataclass
class SigmoidCalibrator:
    coefficient: float
    intercept: float

    @staticmethod
    def _logit(probabilities):
        values = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
        return np.log(values / (1 - values)).reshape(-1, 1)

    @classmethod
    def fit(cls, probabilities, labels, seed=42):
        model = LogisticRegression(random_state=seed).fit(cls._logit(probabilities), labels)
        return cls(float(model.coef_[0, 0]), float(model.intercept_[0]))

    def transform(self, probabilities):
        logits = self._logit(probabilities).ravel()
        value = self.coefficient * logits + self.intercept
        return 1 / (1 + np.exp(-np.clip(value, -35, 35)))


class HistGradientBoostingDecisionModel(BaseDecisionModel):
    family = "sklearn_hist_gradient_boosting"

    def __init__(self, feature_names, medians=None, parameters=None, seed=42,
                 model_version="ml_v1_market", calibrator=None):
        self._feature_names = list(feature_names)
        self.medians = dict(medians or {})
        self.parameters = dict(parameters or {})
        self.seed = int(seed)
        self.model_version = model_version
        self.calibrator = calibrator
        defaults = {"learning_rate": 0.05, "max_iter": 140, "max_leaf_nodes": 15,
                    "min_samples_leaf": 50, "l2_regularization": 1.0,
                    "early_stopping": False, "random_state": self.seed}
        defaults.update(self.parameters)
        self.parameters = defaults
        self.estimator = HistGradientBoostingClassifier(**defaults)

    def _matrix(self, features):
        if isinstance(features, pd.DataFrame):
            missing = set(self._feature_names) - set(features.columns)
            if missing:
                raise ValueError(f"Feature mismatch: {sorted(missing)}")
            matrix = features[self._feature_names].apply(pd.to_numeric, errors="coerce")
            for name in self._feature_names:
                matrix[name] = matrix[name].replace([np.inf, -np.inf], np.nan).fillna(self.medians.get(name, 0.0))
            return matrix.to_numpy(dtype=float)
        matrix = np.asarray(features, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != len(self._feature_names):
            raise ValueError("Feature matrix width does not match model schema")
        return matrix

    def fit(self, features, labels, sample_weight=None):
        self.estimator.fit(self._matrix(features), np.asarray(labels, dtype=int), sample_weight=sample_weight)
        return self

    def raw_predict_proba(self, features):
        return self.estimator.predict_proba(self._matrix(features))[:, 1]

    def predict_proba(self, features):
        probabilities = self.raw_predict_proba(features)
        return self.calibrator.transform(probabilities) if self.calibrator else probabilities

    def predict(self, features, threshold=.5):
        return (self.predict_proba(features) >= threshold).astype(int)

    def prediction_rows(self, features, threshold, timestamps=None, symbols=None):
        probabilities = self.predict_proba(features)
        count = len(probabilities)
        timestamps = timestamps if timestamps is not None else [None] * count
        symbols = symbols if symbols is not None else [None] * count
        return [{"timestamp": None if timestamp is None else str(timestamp), "symbol": symbol,
                 "probability_positive": float(probability), "prediction": int(probability >= threshold),
                 "threshold": float(threshold), "model_version": self.model_version,
                 "calibrated": self.calibrator is not None}
                for timestamp, symbol, probability in zip(timestamps, symbols, probabilities)]

    def feature_names(self):
        return list(self._feature_names)

    def metadata(self):
        return {"model_version": self.model_version, "model_family": self.family,
                "seed": self.seed, "parameters": self.parameters,
                "feature_names": self.feature_names(),
                "calibration": None if self.calibrator is None else
                {"method": "sigmoid", "coefficient": self.calibrator.coefficient,
                 "intercept": self.calibrator.intercept}}

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"estimator": self.estimator, "feature_names": self._feature_names,
                     "medians": self.medians, "parameters": self.parameters, "seed": self.seed,
                     "model_version": self.model_version, "calibrator": self.calibrator}, path)
        return path

    @classmethod
    def load(cls, path):
        payload = joblib.load(path)
        result = cls(payload["feature_names"], payload["medians"], payload["parameters"],
                     payload["seed"], payload["model_version"], payload["calibrator"])
        result.estimator = payload["estimator"]
        return result


def balanced_sample_weights(labels):
    labels = np.asarray(labels, dtype=int)
    counts = np.bincount(labels, minlength=2)
    if not counts[0] or not counts[1]:
        return np.ones(len(labels))
    weights = len(labels) / (2 * counts)
    return weights[labels]
