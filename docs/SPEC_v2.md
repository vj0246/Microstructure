# SPEC v2: Sell-Side Confirmatory Test and Pooled Exploratory Test (frozen at tag `spec-v2`)

Status: frozen 17 September 2026. This file and the code it references are committed under the git tag `spec-v2` before any quantity defined here is computed. `docs/SPEC.md` (tags `spec-v1`, `spec-v1.1`) stays in force for the buy-side test and its results.

## 1. Why a v2, and what had been seen

The v1 primary test (buys) failed its power gate, with I 791 and R 751 after common support, and is reported as descriptive. To gain power without re-testing data already seen, v2 turns to disclosed **sells**.

Seen before this freeze:
- **Buy-side results.** Every v1 outcome by type: θ_I and θ_R, the price path, the placebo loadings, S1 to S4, and the robustness variants.
- **Pooled sell-side returns from the M3 replication.** Gross records, all trader types together, close-to-close. These are not by type.
- **S4.** It used churner-only stock-days (type "none") on both sides, so it is not an I versus R quantity.
- **Event counts.** An outcome-blind power study read group sizes only, after common support, for buys and sells, at type-share thresholds 0.8, 0.67 and 0.5, with and without the R novelty filter.
  - The threshold changes counts by under 3%.
  - Buys cannot reach 1,000 institutional events under any definition.
  - Sells at the unchanged v1 definition give I 1,031 and R 1,067.

**Never computed before this freeze:** any overnight, day-0 or decay return of sell stock-days split by I and R.

## 2. Data version

The data version is the same as v1: `raw/manifest.jsonl`, sha256 `d78737142ebc5aca829db7b98352074ac27c1edd9a443702f734e66f944c34e2`, and the build of 17 September 2026.

## 3. C2: sell-side confirmatory test

- **Cell.** `impact.analysis.estimators.primary_cell(event_table, side=-1)` followed by `common_support`. This is the v1 cell with side = −1: quality conditions, stock-day type I or R at 80% (R also novel), backward-isolated, x inside the overlap of the groups' 1st-to-99th percentile ranges, and buckets holding both groups.
- **Outcome.** y = side · g1_abn / σ = −g1_abn / σ. Positive y means the price keeps moving in the sell's direction after the disclosure.
- **Censoring.** Upper-censored when side · open_band_next > 0 (the next open is at the lower band), lower-censored when it is < 0. Band flags need NSE's `bh` list.
- **Model and estimator.** As in v1: μ = θ_I·1{I}·√x + θ_R·1{R}·√x + λ_bucket; censored Student-t with ν = 4 and scale estimated; covariance clustered two ways, by stock and by date.
- **Hypothesis.** H0: θ_I = θ_R, two-sided Wald test, α = 0.05.
- **Power gate.** If either group has fewer than 1,000 events after common support, C2 is reported as descriptive.
- **Reported with C2, not gated:**
  - the sell-side cumulative path (`stacked_legs` over r_m5, ov, id, g1, d15, d620);
  - the placebo loadings;
  - the v1 robustness variants applied to the sell cell (PLAN section 8 items 1, 2, 4, 9 and 10 in part).

## 4. E1: pooled buys and sells (exploratory)

- **Cell.** The union of the v1 buy cell and the C2 sell cell, each after its own common support.
- **Outcome and censoring.** Signed as in C2.
- **Model.** The same, with bucket × side effects in place of bucket effects, and one θ per type shared across sides.
- **Status.** It reports θ_I − θ_R with its two-way clustered Wald test. E1 reuses buy outcomes already seen, so it is **exploratory**. No claim rests on it alone, and it has no power gate.

## 5. Allowed claims

- If C2 rejects H0, the claim is: "on disclosed sells, the overnight news per unit of disclosed flow differs between institutional and reference sellers."
- The v1 language limits apply, including S1: a type difference may reflect priced future flow rather than fundamental news.
- The typing accuracy caveat carries over. The final audit found 94.5% agreement, with I→R contamination that biases toward no difference.
- A buy-side and a sell-side result may be described together only as a pattern, never as a joint confirmatory test.

## 6. Change log

- 17 September 2026: frozen as `spec-v2`.
