# SPEC v3: Three Mechanism Tests (exploratory, frozen at tag `spec-v3`)

Status: frozen 6 October 2026. This file and the code it references are committed under the git tag `spec-v3` before any quantity defined here is computed. `docs/SPEC.md` (buys) and `docs/SPEC_v2.md` (sells) stay in force for their own tests.

## 1. Status of these tests

All three tests are **exploratory**. They reuse the buy cell (v1) and the sell cell (C2), whose outcomes have already been seen, so none of them can confirm anything. Their purpose is to probe the mechanism behind the v1 and C2 results, with the reading rules fixed in advance so that the reading cannot bend to the result.

Seen before this freeze:
- Every v1 and C2 outcome by type (see the two earlier specs).
- Outcome-blind counts for the cells below: the buy cell has I 791 and R 751, and the sell cell I 1,031 and R 1,067. At price >= Rs 50 the buy cell has I 746 and R 525, and the sell cell I 895 and R 769. At price >= Rs 100 the buy cell has I 660 and R 364, and the sell cell I 748 and R 574.
- Reference buyers trade cheaper stocks: the median prior price is Rs 98 for R and Rs 349 for I (10th percentile Rs 21.6 and Rs 70.8). This is why H1 exists.

Not seen: any g1, day-0 or decay return split by price level or by delivery share.

## 2. Data version

Same as v1 and v2: `raw/manifest.jsonl` sha256 `d78737142ebc5aca829db7b98352074ac27c1edd9a443702f734e66f944c34e2`, and the build of 17 September 2026.

## 3. H1: is the institutional buy premium a tick-size artifact?

**Why.** NSE's tick is Rs 0.05 for these stocks. At Rs 20 that is 25 bp and at Rs 10 it is 50 bp, against a v1 premium of about 15 bp per unit of root flow. Discreteness plus a different price mix could manufacture a gap.

**Cells.** The v1 cell (buys) and the C2 cell (sells), each after common support, with these variants:
- **Primary.** Prior unadjusted price (`prev_last_raw`) >= Rs 50, so the tick is at most 10 bp.
- **Sensitivity 1.** Price >= Rs 100.
- **Sensitivity 2.** Full cell, with ln(prior unadjusted price) added to the design as a control.

**Estimator.** As in v1: censored Student-t, nu = 4, signed outcome, bucket effects, two-way (stock, date) clustering. Common support is not recomputed after the price restriction.

**Reading rule (buys).** Let D be the v1 contrast, theta_I - theta_R = 0.0393 sigma per root flow.
- If the primary contrast is below D / 2, the premium is **tick-driven or price-mix-driven**.
- Otherwise it is **not explained by tick size**.

The test loses power (about 525 reference buys), so the z-statistics are reported next to the reading and both are stated. The sell side is reported with no reading rule, because C2 has no gap to explain.

## 4. H2: does the premium sit in high-delivery events?

**Why.** Institutions take delivery and speculators do not. If the buy premium is information, it should concentrate where the stock-day's delivery share is high.

**Cells.** The v1 cell (primary) and the C2 cell (check), after common support.

**Definition.**
- A stock-day's delivery share is delivered quantity divided by traded quantity for the whole stock-day (not the client's own trade).
- An event is **high** when its delivery share exceeds the median share of all quality stock-days in the same benchmark bucket and year, and **low** otherwise. The median uses every stock-day that passes `quality()`, not just events, and ignores type.
- Events with no delivery data (about 1 per cell) are dropped.
- Common support is determined before the split and not recomputed.

**Model.** The v1 model with four slopes in place of two: theta for each of I-high, I-low, R-high and R-low, on root x, with bucket effects.

**Hypothesis (buys, directional).**
H2: (theta_I,high - theta_R,high) - (theta_I,low - theta_R,low) > 0, tested one-sided at alpha = 0.05 with the same clustering. This is the only inferential test in v3 and is reported without correction. The sell side is reported two-sided with no hypothesis.

## 5. H3: retention with confidence intervals

**Why.** v1 and C2 tested retention only against nulls. Here the cumulative response is reported as levels.

**Cells.** The v1 and C2 cells.

**Method.** `stacked_legs` over (r_m5, ov, id, g1, d15, d620), as in S2. For each type and side, report:
- the cumulative response at t+1 open (the peak), t+5 and t+20, in sigma per root flow, with 95% intervals;
- the share retained at t+20, defined as R(20) divided by the peak, **only when the peak's |z| >= 2**, otherwise "undefined";
- the r_m1 and r_m5 placebo loadings, with the group-side flagged "placebo-clean" only if both p-values exceed 0.05.

No hypothesis. A group-side that is not placebo-clean is described as selection-contaminated.

## 6. Allowed claims

- Nothing here is confirmatory, and none of it changes the verdicts of v1 or C2.
- If H2 rejects, the claim is: "the institutional buy premium is concentrated in high-delivery events, in this sample." If it does not, say so and report the estimate with its interval.
- The typing accuracy caveat carries over: audit agreement 94.5%, with I-to-R contamination that biases toward no difference. The hand dictionary is unverified recollection, shown in the sensitivity run to touch under 1% of events.

## 7. Change log

- 6 October 2026: frozen as `spec-v3`.
