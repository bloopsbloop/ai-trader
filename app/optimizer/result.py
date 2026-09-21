import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CombinationResult:
    parameters: dict
    train_metrics: object
    score: float

    def as_dict(self):
        return {"parameters": self.parameters, "train_metrics": self.train_metrics.as_dict(), "score": self.score}


@dataclass
class OptimizationResult:
    strategy_name: str
    best_parameters: dict
    train_metrics: object
    validation_metrics: object
    test_metrics: object
    score: float
    tested_combinations: list
    top_combinations: list = field(default_factory=list)

    def export_csv(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        parameter_names = sorted({name for result in self.tested_combinations for name in result.parameters})
        metric_names = tuple(self.train_metrics.as_dict())
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["score", *(f"param_{name}" for name in parameter_names), *metric_names])
            writer.writeheader()
            for result in self.tested_combinations:
                row = {"score": result.score, **{f"param_{name}": value for name, value in result.parameters.items()}}
                row.update(result.train_metrics.as_dict())
                writer.writerow(row)
        return path

    def export_json(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"strategy": self.strategy_name, "best_parameters": self.best_parameters, "score": self.score, "train_metrics": self.train_metrics.as_dict(), "validation_metrics": self.validation_metrics.as_dict(), "test_metrics": self.test_metrics.as_dict(), "tested_combinations": [result.as_dict() for result in self.tested_combinations]}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def format_report(self):
        return "\n".join(["=" * 40, "STRATEGY OPTIMIZATION", "=" * 40, f"Strategy: {self.strategy_name}", f"Combinations tested: {len(self.tested_combinations)}", "", "Best parameters:", *[f"{name}: {value}" for name, value in self.best_parameters.items()], "", self._format_metrics("TRAIN", self.train_metrics), "", self._format_metrics("VALIDATION", self.validation_metrics), "", self._format_metrics("TEST", self.test_metrics), "=" * 40])

    @staticmethod
    def _format_metrics(name, metrics):
        profit_factor = "∞" if metrics.profit_factor == float("inf") else f"{metrics.profit_factor:.2f}"
        return "\n".join([name, f"Return        : {metrics.total_return:.2f}%", f"Max Drawdown  : {metrics.max_drawdown:.2f}%", f"Sharpe        : {metrics.sharpe_ratio:.2f}", f"Profit Factor : {profit_factor}"])

    def print_report(self):
        report = self.format_report()
        print(report)
        return report
