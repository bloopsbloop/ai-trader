# M21.2 — Design Addendum to the M21.1 Conditional Draft

This document was fixed before viewing M21.2 endpoint responses. It corrects ambiguities in the M21.1 proposal but is not a preregistration and does not authorize acquisition beyond the bounded probe or any economic calculation.

## Signal sign and funding units

The proposed funding condition must satisfy both `fundingRate <= expanding 5th percentile of strictly earlier eligible observations` **and** `fundingRate < 0`. A low percentile can be positive and would not represent the hypothesized negative-funding crowding state. No other percentile or holding horizon is searched.

The reported `fundingRate` is a rate for its realized settlement interval. Comparable intensity requires an interval duration inferred only from timestamps and metadata already available by the signal time. A historical signal may use the interval ending at its own `fundingTime` only when the start or duration of that interval is knowable at or before that settlement; it may not use the next settlement timestamp to infer the preceding interval. Current `fundingInfo` is not proof of an old interval schedule. If historical metadata or prior timestamps cannot establish the applicable interval without looking forward, cadence normalization is blocked; the later protocol must either compare like-duration settlements explicitly or declare the estimate unusable. An eight-hour cadence is never assumed globally.

## Availability and entry chronology

Four times remain distinct:

1. `fundingTime`, the settlement timestamp attached to a realized funding row;
2. premium-bar close time, the end of the selected completed 1h premium bar;
3. assumed data-availability time, which may be later than either source timestamp;
4. Spot entry time, the open of the first bar eligible after the availability rule and embargo.

Concrete example: for a funding row timestamped `2026-01-15T08:00:00Z`, the premium observation is the bar opened `2026-01-15T08:00:00Z` and closed `2026-01-15T08:59:59.999Z`. Under the draft's one-complete-hour embargo, the first eligible entry is the Spot 1h bar opening `2026-01-15T10:00:00Z`, provided both inputs are assumed available before then. The hour between the premium close and entry is a conservative design assumption, not a measured publication-latency guarantee.

Today's corrected historical responses cannot by themselves prove that identical values were available, immutable and revision-free at the historical decision time. Retrieval timestamps and response hashes document what was seen now.

## Missing execution data

Eligibility is determined using information available at entry. An entry that was valid at that time is not removed later because its required exit bar is missing. The event is recorded as unresolved/censored with the reason and capital exposure retained in the accounting record. A later protocol must preselect one defensible treatment: conservative explicit return bounds based on information available without inventing a fill, or a declaration that the primary estimate is unusable when unresolved exposure exceeds a fixed tolerance. It may not substitute a convenient later price, silently close the position, hide it from the denominator or choose the treatment after seeing outcomes.

## Portfolio accounting and baselines

The primary investable object should be a full-calendar portfolio, distinct from the descriptive mean event return:

- initial capital is split 50/50 into fixed BTC and ETH sleeves;
- each sleeve is fully invested in its asset during an eligible, non-overlapping event and otherwise remains cash at 0%;
- simultaneous BTC and ETH positions retain their fixed sleeve weights; unused sleeve capital stays cash;
- entry/exit costs are charged only to the traded sleeve; USD-M funding payments are never included because the position is Spot;
- portfolio return compounds across every calendar interval, including intervals when one or both sleeves are cash.

Cash is the primary baseline fixed without future data. A simple equal-weight BTC/ETH passive baseline, continuously held and rebalanced on a calendar schedule fixed before evaluation, may be a second primary economic hurdle with explicit comparable Spot costs. The M21.1 exposure-matched passive construction depends on realized future strategy exposure and is therefore retained only as a retrospective diagnostic control; it cannot be the sole primary baseline.

Portfolio inference must preserve the complete calendar, cash periods and contemporaneous BTC/ETH dependence. Resampling assets or events independently is invalid. A calendar-block procedure must operate on joint portfolio increments, with block length and incomplete-block handling frozen before outcomes.

If an event-level endpoint is retained as secondary, its denominator is every chronologically eligible entry, including unresolved/censored entries flagged under the fixed missing-data rule. Mean event return is calculated over resolved events with resolved count and total eligible count both displayed. Dependence is handled by calendar clusters containing all overlapping BTC and ETH events, not by treating event rows or assets as independent.

Twelve future months are a calendar design, not a power guarantee. A small negative estimate with a wide interval is an uncertain result, not statistical proof that the mechanism is absent. Likewise, a positive estimate does not establish a trading edge unless the later frozen statistical and economic hurdles are met.

## Consequence for later work

Before a historical experiment could be preregistered, it would need a point-in-time-safe rule for cadence, a documented availability convention, a frozen missing-exit treatment, full-calendar accounting, and terms/provenance sufficient for the intended research. M21.2 may diagnose these conditions only; it does not resolve them by inspecting returns or fitting any parameter.
