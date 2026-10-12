from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from app.research.funding_access_probe import (
    HOUR_MS,
    RequestSpec,
    analyze_funding,
    analyze_klines,
    pagination_second_spec,
    prepare_output,
    run_probe,
)


def funding_row(timestamp: int) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT", "fundingRate": "-0.00010000",
        "fundingTime": timestamp, "markPrice": "30000.0", "rateType": "Regular",
    }


def kline(timestamp: int) -> list[object]:
    return [timestamp, "1", "2", "0.5", "1.5", "10", timestamp + HOUR_MS - 1,
            "15", 3, "5", "7.5", "0"]


def test_pagination_advances_one_millisecond_after_last_record() -> None:
    page = [funding_row(1688169600000), funding_row(1688198400000)]
    second = pagination_second_spec(page)
    assert second.params["startTime"] == 1688198400001
    assert second.params["limit"] == 2


def test_funding_qa_reports_duplicates_and_observed_intervals() -> None:
    start = 1688169600000
    records = [funding_row(start), funding_row(start), funding_row(start + 8 * HOUR_MS)]
    result = analyze_funding(records, start, start + 2 * 24 * HOUR_MS)
    assert result["duplicate_timestamps"] == 1
    assert result["observed_interval_ms"] == [0, 8 * HOUR_MS]
    assert result["rate_types"] == ["Regular"]


def test_funding_qa_preserves_documented_string_with_empty_historical_mark_price() -> None:
    start = 1688169600000
    row = funding_row(start)
    row["markPrice"] = ""
    result = analyze_funding([row], start, start + HOUR_MS)
    assert result["empty_mark_prices"] == 1


def test_kline_qa_validates_timestamp_bounds_and_gaps() -> None:
    start = 1688169600000
    result = analyze_klines([kline(start), kline(start + 2 * HOUR_MS)],
                            start, start + 3 * HOUR_MS)
    assert result["one_hour_gaps"] == 1
    assert result["invalid_one_hour_close_times"] == 0
    with pytest.raises(ValueError, match="outside requested bounds"):
        analyze_klines([kline(start - HOUR_MS)], start, start + HOUR_MS)


def test_output_is_create_only(tmp_path: Path) -> None:
    output = tmp_path / "probe"
    assert prepare_output(output) == output.resolve()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        prepare_output(output)


class FakeResponse:
    def __init__(self, status_code: int, value: object) -> None:
        self.status_code = status_code
        self.content = json.dumps(value).encode()
        self.headers = {"Content-Type": "application/json"}


class RestrictedSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, params: dict[str, object], timeout: tuple[int, int]) -> FakeResponse:
        self.calls.append(url)
        if "fundingRate" in url:
            return FakeResponse(451, {"msg": "not available in your region"})
        return FakeResponse(200, [])


def test_regional_restriction_stops_only_that_endpoint_family(tmp_path: Path) -> None:
    session = RestrictedSession()
    result = run_probe(tmp_path / "probe", session=session, sleeper=lambda _: None)
    assert result["blocked_endpoint_families"] == ["funding"]
    assert sum("fundingRate" in url for url in session.calls) == 1
    assert result["http_attempts"] == 13  # one funding plus all twelve kline requests


class FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, params: dict[str, object], timeout: tuple[int, int]) -> FakeResponse:
        self.calls += 1
        raise requests.ConnectionError("offline")


def test_transport_failures_are_bounded_by_global_attempt_budget(tmp_path: Path) -> None:
    session = FailingSession()
    with pytest.raises(RuntimeError, match="attempt budget exhausted"):
        run_probe(tmp_path / "probe", session=session, sleeper=lambda _: None)
    assert session.calls == 36
