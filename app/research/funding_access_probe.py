"""Bounded, non-economic M21.2 probe of three public market-data endpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

OUTPUT_NAMESPACE = "m21_2_funding_access_probe_v1"
MAX_ATTEMPTS = 36
MAX_OUTPUT_BYTES = 50 * 1024 * 1024
TIMEOUT = (5, 15)
RETRIES = 1
RETRY_DELAY_SECONDS = 2
HOUR_MS = 3_600_000

WINDOWS = {
    "beginning": (1577836800000, 1578009600000),  # 2020-01-01 through 2020-01-03 UTC
    "middle": (1688169600000, 1688342400000),     # 2023-07-01 through 2023-07-03 UTC
    "end": (1790812800000, 1790985600000),        # 2026-10-01 through 2026-10-03 UTC
}
SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class RequestSpec:
    request_id: str
    family: str
    symbol: str
    window: str
    url: str
    params: dict[str, Any]
    purpose: str = "window"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def prepare_output(root: Path) -> Path:
    target = root.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite probe output: {target}")
    (target / "raw").mkdir(parents=True, exist_ok=False)
    return target


def base_schedule() -> list[RequestSpec]:
    specs: list[RequestSpec] = []
    for window, (start, end_exclusive) in WINDOWS.items():
        end = end_exclusive - 1
        for symbol in SYMBOLS:
            specs.extend([
                RequestSpec(
                    f"funding_{symbol.lower()}_{window}", "funding", symbol, window,
                    "https://fapi.binance.com/fapi/v1/fundingRate",
                    {"symbol": symbol, "startTime": start, "endTime": end, "limit": 1000},
                ),
                RequestSpec(
                    f"premium_{symbol.lower()}_{window}", "premium", symbol, window,
                    "https://fapi.binance.com/fapi/v1/premiumIndexKlines",
                    {"symbol": symbol, "interval": "1h", "startTime": start,
                     "endTime": end, "limit": 1500},
                ),
                RequestSpec(
                    f"spot_{symbol.lower()}_{window}", "spot", symbol, window,
                    "https://api.binance.com/api/v3/klines",
                    {"symbol": symbol, "interval": "1h", "startTime": start,
                     "endTime": end, "timeZone": "0", "limit": 1000},
                ),
            ])
    return specs


def pagination_first_spec() -> RequestSpec:
    start, end_exclusive = WINDOWS["middle"]
    return RequestSpec(
        "funding_btcusdt_middle_page1", "funding", "BTCUSDT", "middle",
        "https://fapi.binance.com/fapi/v1/fundingRate",
        {"symbol": "BTCUSDT", "startTime": start, "endTime": end_exclusive - 1, "limit": 2},
        "pagination_page_1",
    )


def pagination_second_spec(page_one: list[dict[str, Any]]) -> RequestSpec:
    if not page_one:
        raise ValueError("cannot derive page 2 from an empty page 1")
    last_time = page_one[-1].get("fundingTime")
    if not isinstance(last_time, int):
        raise ValueError("page 1 has no integer fundingTime")
    _, end_exclusive = WINDOWS["middle"]
    return RequestSpec(
        "funding_btcusdt_middle_page2", "funding", "BTCUSDT", "middle",
        "https://fapi.binance.com/fapi/v1/fundingRate",
        {"symbol": "BTCUSDT", "startTime": last_time + 1,
         "endTime": end_exclusive - 1, "limit": 2},
        "pagination_page_2",
    )


def repeat_spec() -> RequestSpec:
    original = next(
        spec for spec in base_schedule()
        if spec.request_id == "funding_ethusdt_middle"
    )
    return RequestSpec(
        "funding_ethusdt_middle_repeat", original.family, original.symbol,
        original.window, original.url, dict(original.params), "exact_repeat",
    )


def canonical_records(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _decimal_string(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    try:
        Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not decimal text") from exc


def analyze_funding(records: Any, start: int, end: int) -> dict[str, Any]:
    if not isinstance(records, list):
        raise ValueError("funding response must be a list")
    times: list[int] = []
    rate_types: set[str] = set()
    fields: set[str] = set()
    empty_mark_prices = 0
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("funding row must be an object")
        required = {"symbol", "fundingRate", "fundingTime", "markPrice"}
        if not required.issubset(row):
            raise ValueError(f"funding row missing fields: {sorted(required - set(row))}")
        if not isinstance(row["symbol"], str) or not isinstance(row["fundingTime"], int):
            raise ValueError("funding symbol/time types are invalid")
        _decimal_string(row["fundingRate"], "fundingRate")
        if row["markPrice"] == "":
            empty_mark_prices += 1
        else:
            _decimal_string(row["markPrice"], "markPrice")
        if "rateType" in row:
            if not isinstance(row["rateType"], str):
                raise ValueError("rateType must be a string")
            rate_types.add(row["rateType"])
        times.append(row["fundingTime"])
        fields.update(row)
    if any(timestamp < start or timestamp > end for timestamp in times):
        raise ValueError("funding timestamp outside requested bounds")
    deltas = [right - left for left, right in zip(times, times[1:])]
    return {
        "rows": len(records),
        "fields": sorted(fields),
        "timestamps_sorted": times == sorted(times),
        "duplicate_timestamps": len(times) - len(set(times)),
        "first_timestamp": times[0] if times else None,
        "last_timestamp": times[-1] if times else None,
        "observed_interval_ms": sorted(set(deltas)),
        "rate_types": sorted(rate_types),
        "empty_mark_prices": empty_mark_prices,
    }


def analyze_klines(records: Any, start: int, end: int) -> dict[str, Any]:
    if not isinstance(records, list):
        raise ValueError("kline response must be a list")
    opens: list[int] = []
    close_times: list[int] = []
    for row in records:
        if not isinstance(row, list) or len(row) != 12:
            raise ValueError("kline row must be a 12-element list")
        if not isinstance(row[0], int) or not isinstance(row[6], int):
            raise ValueError("kline timestamps must be integers")
        for index in (1, 2, 3, 4, 5, 7, 9, 10, 11):
            _decimal_string(row[index], f"kline[{index}]")
        if not isinstance(row[8], int):
            raise ValueError("kline trade/count field must be an integer")
        opens.append(row[0])
        close_times.append(row[6])
    if any(timestamp < start or timestamp > end for timestamp in opens):
        raise ValueError("kline open timestamp outside requested bounds")
    deltas = [right - left for left, right in zip(opens, opens[1:])]
    bad_durations = sum(close != opened + HOUR_MS - 1
                        for opened, close in zip(opens, close_times))
    return {
        "rows": len(records),
        "tuple_length": 12,
        "timestamps_sorted": opens == sorted(opens),
        "duplicate_open_times": len(opens) - len(set(opens)),
        "first_open_time": opens[0] if opens else None,
        "last_open_time": opens[-1] if opens else None,
        "observed_open_interval_ms": sorted(set(deltas)),
        "one_hour_gaps": sum(delta != HOUR_MS for delta in deltas),
        "invalid_one_hour_close_times": bad_durations,
        "open_times": opens,
    }


def _restriction(status: int, body: bytes) -> bool:
    if status == 451:
        return True
    if status not in (401, 403):
        return False
    text = body.decode("utf-8", errors="ignore").lower()
    return any(token in text for token in
               ("restricted location", "not available in your region", "jurisdiction", "eligibility"))


def _selected_headers(headers: Any) -> dict[str, str]:
    allowed = {"content-type", "date", "server", "x-mbx-used-weight-1m",
               "x-mbx-used-weight", "retry-after"}
    return {str(key): str(value) for key, value in headers.items()
            if str(key).lower() in allowed}


def _write_bytes(path: Path, value: bytes, bytes_written: int) -> int:
    projected = bytes_written + len(value)
    if projected > MAX_OUTPUT_BYTES:
        raise RuntimeError("probe output budget would be exceeded")
    path.write_bytes(value)
    return projected


def run_probe(
    output_dir: Path,
    session: Any | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    output = prepare_output(output_dir)
    client = session or requests.Session()
    attempts = 0
    raw_bytes = 0
    blocked_families: set[str] = set()
    request_log: list[dict[str, Any]] = []
    responses: dict[str, Any] = {}

    def fetch(spec: RequestSpec) -> Any | None:
        nonlocal attempts, raw_bytes
        if spec.family in blocked_families:
            request_log.append({"request_id": spec.request_id, "family": spec.family,
                                "status": "skipped_after_restriction", "attempted": False})
            return None
        for retry in range(RETRIES + 1):
            if attempts >= MAX_ATTEMPTS:
                raise RuntimeError("HTTP attempt budget exhausted")
            attempts += 1
            retrieved = _iso_utc()
            entry: dict[str, Any] = {
                "request_id": spec.request_id, "family": spec.family,
                "symbol": spec.symbol, "window": spec.window, "purpose": spec.purpose,
                "url": spec.url, "params": dict(spec.params), "retrieved_at_utc": retrieved,
                "attempt": retry + 1, "attempted": True,
            }
            try:
                response = client.get(spec.url, params=spec.params, timeout=TIMEOUT)
                body = bytes(response.content)
                raw_path = output / "raw" / f"{attempts:02d}_{spec.request_id}_attempt{retry + 1}.body"
                raw_bytes = _write_bytes(raw_path, body, raw_bytes)
                entry.update({
                    "status": int(response.status_code),
                    "headers": _selected_headers(response.headers),
                    "raw_file": raw_path.relative_to(output).as_posix(),
                    "body_bytes": len(body), "sha256": sha256_bytes(body),
                })
                request_log.append(entry)
                with (output / "attempt_log.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if _restriction(response.status_code, body):
                    blocked_families.add(spec.family)
                    return None
                if response.status_code == 200:
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"non-JSON success response for {spec.request_id}") from exc
                if response.status_code == 429 or response.status_code >= 500:
                    if retry < RETRIES:
                        sleeper(RETRY_DELAY_SECONDS)
                        continue
                return None
            except requests.RequestException as exc:
                entry.update({"status": "transport_error", "error_type": type(exc).__name__,
                              "body_bytes": 0, "sha256": None})
                request_log.append(entry)
                with (output / "attempt_log.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if retry < RETRIES:
                    sleeper(RETRY_DELAY_SECONDS)
                    continue
                return None
        return None

    for spec in base_schedule():
        responses[spec.request_id] = fetch(spec)

    page_one_spec = pagination_first_spec()
    page_one = fetch(page_one_spec)
    responses[page_one_spec.request_id] = page_one
    if isinstance(page_one, list) and page_one:
        page_two_spec = pagination_second_spec(page_one)
        responses[page_two_spec.request_id] = fetch(page_two_spec)
    else:
        request_log.append({"request_id": "funding_btcusdt_middle_page2",
                            "family": "funding", "status": "skipped_no_page_1",
                            "attempted": False})
        responses["funding_btcusdt_middle_page2"] = None

    repeated = repeat_spec()
    responses[repeated.request_id] = fetch(repeated)

    qa: dict[str, Any] = {"requests": {}, "alignments": {}, "pagination": {}, "repeat": {}}
    for spec in base_schedule():
        records = responses.get(spec.request_id)
        if records is None:
            qa["requests"][spec.request_id] = {"usable": False}
            continue
        start, end_exclusive = WINDOWS[spec.window]
        analysis = (analyze_funding(records, start, end_exclusive - 1)
                    if spec.family == "funding"
                    else analyze_klines(records, start, end_exclusive - 1))
        analysis["usable"] = True
        qa["requests"][spec.request_id] = analysis

    for window in WINDOWS:
        for symbol in SYMBOLS:
            premium = qa["requests"].get(f"premium_{symbol.lower()}_{window}", {})
            spot = qa["requests"].get(f"spot_{symbol.lower()}_{window}", {})
            if premium.get("usable") and spot.get("usable"):
                premium_opens = set(premium.pop("open_times"))
                spot_opens = set(spot.pop("open_times"))
                qa["alignments"][f"{symbol}_{window}"] = {
                    "premium_rows": len(premium_opens), "spot_rows": len(spot_opens),
                    "common_open_times": len(premium_opens & spot_opens),
                    "premium_only_open_times": len(premium_opens - spot_opens),
                    "spot_only_open_times": len(spot_opens - premium_opens),
                }

    page_one = responses.get("funding_btcusdt_middle_page1")
    page_two = responses.get("funding_btcusdt_middle_page2")
    if isinstance(page_one, list) and isinstance(page_two, list):
        first_times = [row["fundingTime"] for row in page_one]
        second_times = [row["fundingTime"] for row in page_two]
        qa["pagination"] = {
            "page_1_rows": len(first_times), "page_2_rows": len(second_times),
            "overlap_count": len(set(first_times) & set(second_times)),
            "strict_boundary": bool(not second_times or (first_times and second_times[0] > first_times[-1])),
        }
    original = responses.get("funding_ethusdt_middle")
    repeated_value = responses.get("funding_ethusdt_middle_repeat")
    if original is not None and repeated_value is not None:
        qa["repeat"] = {
            "canonical_equal": canonical_records(original) == canonical_records(repeated_value),
            "original_sha256": sha256_bytes(canonical_records(original)),
            "repeat_sha256": sha256_bytes(canonical_records(repeated_value)),
        }

    summary = {
        "version": "m21.2-funding-access-probe-v1",
        "created_at_utc": _iso_utc(),
        "logical_requests_planned": 21,
        "http_attempts": attempts,
        "raw_body_bytes": raw_bytes,
        "blocked_endpoint_families": sorted(blocked_families),
        "qa": qa,
    }
    (output / "request_log.json").write_text(
        json.dumps(request_log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "qa_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total_output = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    if total_output > MAX_OUTPUT_BYTES:
        raise RuntimeError("probe output budget exceeded after metadata write")
    summary["total_output_bytes"] = total_output
    (output / "qa_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("data/research") / OUTPUT_NAMESPACE)
    args = parser.parse_args(list(argv) if argv is not None else None)
    summary = run_probe(args.output)
    print(json.dumps({key: summary[key] for key in
                      ("http_attempts", "raw_body_bytes", "total_output_bytes",
                       "blocked_endpoint_families")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
