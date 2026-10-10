# WSL Cross-Platform Manifest Path Fix Addendum

Date: 2026-10-10 (Europe/Vienna)

This addendum preserves `docs/WSL_TEST_VERIFICATION.md` as the historical first WSL run and records the minimal fix for its only failure.

## Cause and fix

`docs/M17_1R_BASELINE_HASHES.json` intentionally remains frozen and contains Windows-style relative paths. The artifact integrity test previously passed those strings directly to the host `pathlib.Path`; on Linux, backslashes are filename characters, so the first real fixture could not be opened even though it existed and its SHA-256 matched.

The manifest has no production consumer. The fix therefore adds `tests/artifact_manifest.py`, a test-only helper that:

- parses both Windows-style and POSIX-style relative manifest paths;
- resolves them below an explicit fixture/project root;
- rejects drive-qualified, root-qualified, absolute, UNC, empty, and `..` paths;
- confirms the resolved path remains below the allowed root;
- reads the actual file bytes and compares their SHA-256 to the frozen expected value.

The frozen JSON, expected hashes, datasets, models, and artifacts were not changed. Missing files still raise `FileNotFoundError`; hash mismatches still raise `AssertionError`. No assertion was weakened and no skip was added.

## Exact test basis

- Research source: `c497d40e4cbf4c370e0746e540082ded8f093757`
- Documentation base: `50fd385a2b4f20fa3ef376d49c3d3552479e79f5`
- Tested code patch files: `tests/artifact_manifest.py`, `tests/test_artifact_manifest.py`, and `tests/test_absolute_trend.py`
- SHA-256 of the staged three-file binary Git patch: `bcc93ee30c8ada56fc284af9abb78b0409ba0ccb27c6e36fc10e2431673d4d2f`
- Isolated WSL copy: `/home/ostap/ai_trader_wsl_c497d40e`

The WSL copy remained based on the research source. Only the three tested patch files were copied into it; fixtures and dependencies were retained from the original WSL verification.

## Verification

Windows targeted command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_artifact_manifest.py tests\test_absolute_trend.py
```

Result: **26 passed in 1.53s**.

WSL targeted command:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_artifact_manifest.py tests/test_absolute_trend.py
```

Result: **26 passed in 0.96s**.

WSL full command:

```bash
cd /home/ostap/ai_trader_wsl_c497d40e
export PYTHONDONTWRITEBYTECODE=1
set -o pipefail
.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tee pytest-path-fix-full.log
```

Result: **299 passed, 0 failed, 0 skipped, 3 warnings in 24.38s**. The warnings are the existing `python-binance`/`websockets` deprecations documented in the original report. Full log SHA-256: `54f186509a6b82cb890a30ac4d9a0075a54330548d879b63a15f994eb08c0593`.

Post-suite checks:

- `pip check`: `No broken requirements found.`
- Frozen fixture verification: **55/55 matched**, 94,956,200 bytes.
- Fixture manifest SHA-256: `0f4c9534fc76afb0ae99c7371b1c8e461b054b8e568d2f62ac3592c8ce23796b`.
- `git diff --check`: passed.

## Scope and remaining limitations

Windows Smart App Control remains enabled and the Windows scikit-learn WinError 4551 remains unchanged; WSL continues to provide the verified Linux test environment. Linux uses Python 3.14.4 rather than the Windows checkpoint's 3.14.6 and the official `xgboost-cpu==3.4.1` distribution, as recorded in the original report.

No Oracle/server change, trading process, Batch 3 integration, `.env`/SQLite/security change, training, research pipeline, or new research milestone was performed.
