# WSL Test Verification

Date: 2026-10-10 (Europe/Vienna)

## Result

The full project test suite was executed in an isolated WSL2 copy. No tests or ML modules were excluded, and no assertions or skips were added.

- **289 passed, 1 failed, 0 skipped, 3 warnings** in 37.40 seconds.
- The sole failure is a cross-platform path issue in `test_frozen_artifacts_match_review_baseline_hashes`.
- All 55 required frozen fixture hashes were independently verified before pytest and match the published baseline.
- No code failure was observed in the other 289 tests, including the scikit-learn and XGBoost tests that Windows Smart App Control prevented from collecting.

The failing test reads Windows-style path strings from `docs/M17_1R_BASELINE_HASHES.json` directly with `pathlib.Path`. On Linux, `data\history\BNBUSDT_4h.csv` is treated as a filename containing backslashes rather than `data/history/BNBUSDT_4h.csv`, producing `FileNotFoundError`. The fixture exists at the correct Linux path and its SHA-256 matches. The test, baseline JSON, and assertions were left unchanged; no dummy artifacts or compatibility aliases were created.

## Safety and scope

- Windows Smart App Control was not disabled or changed. Post-test checks report `SmartAppControlState: On` and `VerifiedAndReputablePolicyState: 1`.
- The known Windows block remains: policy `{0283AC0F-FFF1-49AE-ADA1-8A933130CAD6}` (`VerifiedAndReputable Desktop`) blocks the Windows scikit-learn `_criterion.cp314-win_amd64.pyd` with WinError 4551. The earlier recorded file SHA-256 is `550a691665862083515e9f760bcc347bb35cf6aad9d54e49cbcbb676d13515b2`.
- No Oracle or server access occurred.
- No live trading, paper/shadow process, Batch 3 integration, training, or research pipeline was started.
- No market data was downloaded.
- The Windows `.venv`, `.env`, SQLite files, research outputs, models, and source checkout were not written by pytest.
- Ubuntu package installation affected only WSL. The original Windows checkout remained the source of truth.

## Source under test

- Repository: `https://github.com/bloopsbloop/ai-trader.git`
- Branch at copy time: `main`
- Commit: `c497d40e4cbf4c370e0746e540082ded8f093757`
- Local `HEAD` and `origin/main` matched, and the tracked working tree was clean before documentation was added.
- Isolated Linux copy: `/home/ostap/ai_trader_wsl_c497d40e`
- The copy was created from `git archive` at the exact commit. The tracked `.env` and tracked Windows `__pycache__` files were explicitly excluded.

Required data was determined by reading the tests before pytest. Only paths named by `docs/M17_1R_BASELINE_HASHES.json` were copied from the clean Windows checkout: 7 OHLCV files, 15 M17.1 original artifacts, 32 M16.2 key artifacts, and 1 protocol file. Total: 55 files, 94,956,200 bytes. No SQLite file was copied.

Exact per-file sizes and SHA-256 values are recorded in `docs/WSL_TEST_FIXTURE_HASHES.json` (manifest SHA-256 `0f4c9534fc76afb0ae99c7371b1c8e461b054b8e568d2f62ac3592c8ce23796b`). Some tracked documentation was copied from the clean Windows checkout to preserve the CRLF byte representation used by the published hashes; `git archive` otherwise materializes LF bytes.

## Linux environment

- Distribution: Ubuntu 26.04.1 LTS, WSL2
- WSL version: 3.0.1.0
- Kernel: `6.18.40.1-microsoft-standard-WSL2`
- Interpreter: `/home/ostap/ai_trader_wsl_c497d40e/.venv/bin/python`
- System interpreter target: `/usr/bin/python3.14`
- Linux Python: 3.14.4 (`3.14.4-1ubuntu0.2`)
- Windows checkpoint Python: 3.14.6
- pip: 26.0.1

The Python patch version differs because Ubuntu 26.04 provides 3.14.4. Both use the CPython 3.14 ABI. All Python packages match the Windows checkpoint versions except that Linux uses the official CPU-only distribution `xgboost-cpu==3.4.1` in place of `xgboost==3.4.1`. It exposes `xgboost.__version__ == 3.4.1` without adding the ordinary Linux wheel's NVIDIA NCCL dependency. The complete reproducible package list is `docs/WSL_TEST_REQUIREMENTS.txt`.

Key versions:

- NumPy 2.5.3
- SciPy 1.18.1
- pandas 3.0.6
- scikit-learn 1.9.1
- XGBoost 3.4.1 CPU
- pytest 9.1.1

`pip check` reported `No broken requirements found.` All required imports succeeded. scikit-learn loaded `_criterion.cpython-314-x86_64-linux-gnu.so`, confirmed as an x86-64 ELF shared object; no Windows PE or `.pyd` binary was used.

During `python3.14-venv` installation, Ubuntu emitted a failed `systemd-binfmt.service` trigger. It did not affect Python, native imports, or pytest. No systemd service for this project was created or changed.

## Pytest command and output

Executed from the isolated Linux copy:

```bash
cd /home/ostap/ai_trader_wsl_c497d40e
export PYTHONDONTWRITEBYTECODE=1
set -o pipefail
.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tee pytest-full.log
```

Result:

```text
FAILED tests/test_absolute_trend.py::test_frozen_artifacts_match_review_baseline_hashes
1 failed, 289 passed, 3 warnings in 37.40s
```

The three warnings are existing deprecations from `python-binance`/`websockets`: two references to `WebSocketClientProtocol` and one to `websockets.legacy`.

The isolated log SHA-256 is `f8aff9f9277ba1ae8be8e0de61952b01eaf3ba16b3d418182fc3fa8c68a52a31`.

## Reproduction

Run these commands in WSL. They contain no credentials:

```bash
sudo apt-get update
sudo apt-get install -y python3.14-venv

SOURCE=/mnt/c/Users/ostap/PycharmProjects/Al_Trader
ROOT=/home/ostap/ai_trader_wsl_c497d40e
COMMIT=c497d40e4cbf4c370e0746e540082ded8f093757
mkdir -p "$ROOT"
git -c safe.directory="$SOURCE" -C "$SOURCE" archive "$COMMIT" -- . \
  ':(exclude).env' ':(exclude,glob)**/__pycache__/**' | tar -x -C "$ROOT"

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --index-url https://pypi.org/simple pip==26.0.1
"$ROOT/.venv/bin/python" -m pip install --index-url https://pypi.org/simple \
  -r "$SOURCE/docs/WSL_TEST_REQUIREMENTS.txt"
"$ROOT/.venv/bin/python" -m pip check
```

Before pytest, copy only the files enumerated by `docs/WSL_TEST_FIXTURE_HASHES.json` from the Windows source into the same normalized relative paths under `$ROOT`, and verify every SHA-256. This preserves the required frozen checkpoint without copying `.env`, SQLite, credentials, or the full ignored `data/` tree.

Then run the pytest command above. The single path-separator failure is expected until the test or baseline path format is made platform-neutral in a separate code change.
