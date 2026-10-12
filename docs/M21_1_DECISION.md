# M21.1 — Decision

## Status

**`CONDITIONAL_ON_DATA_ACCESS`**

Exactly one next experiment is conditionally supportable: a fixed BTCUSDT/ETHUSDT Spot long-or-cash test after extreme negative USD-M funding and negative premium. It is not preregistered and must not run from this decision.

## Why the status is conditional

Before preregistration, a separate non-economic acquisition check must establish all of the following without inspecting forward returns:

1. The public funding-rate, premium-index-kline and Spot-kline endpoints are accessible in the user's region without an account or API key.
2. BTCUSDT and ETHUSDT have sufficient common timestamp coverage, with exact earliest/latest timestamps, missing intervals, duplicates and `rateType` values recorded.
3. Timestamp-pagination can resume deterministically and raw responses can be hashed without silent replacement.
4. The applicable API/product terms permit the intended personal historical research and do not make the eventual research-to-live path unusable. M21.1 makes no legal determination.
5. A fixed future evaluation period can begin only after the acquisition code, rule and costs are frozen. Previously viewed 2023–2026 outcomes remain exploratory.

Failure of conditions 1–4 changes the status to `NO_JUSTIFIED_EXPERIMENT`; it does not justify substituting Binance Vision archives silently, because their current dataset terms contain a material live-use restriction.

## Why the other candidates are not selected

- Calendar seasonality has cheap data but no justified directional cell. Searching the hour/day grid on viewed OHLCV would reopen tuning under a new label.
- On-chain exchange flow is economically distinct, but immutable point-in-time data requires an unresolved paid entitlement and API key; mutable reconstructed histories create revision leakage.

## What changed from M19.1

M19.1 correctly found no runnable experiment among cross-venue discovery, depth resilience and listing drift. M21.1 changes the operational constraint: it searches only batch-retrievable historical observables and identifies derivative funding/premium as a new, economically distinct input that does not require continuous capture. The result is still conditional because documentation alone does not prove coverage, access or permissible downstream use.

## Stop boundary

No data acquisition, backtest, training, PnL calculation or parameter selection is authorized. The next permissible action would be a narrowly specified source/access/provenance probe followed by review. It must not calculate returns or choose a threshold. If the conditions pass, a later document may preregister the proposed experiment; M21.1 itself does not.
