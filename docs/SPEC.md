# SPEC: Pre-registered Confirmatory Test (frozen at tag `spec-v1`)

Status: frozen 17 September 2026 at milestone M4. The draft was written on 14 September 2026 from `PLAN.md` sections 5 to 8. The file and the code it references are committed together under the git tag `spec-v1`. Before that tag, nothing may compute the overnight leg `g1` split by trader type. Any change after the tag needs a new tag and a dated note at the end of this file.

## 1. Data version

- Raw store: `raw/manifest.jsonl` with 14,940 files, sha256 `d78737142ebc5aca829db7b98352074ac27c1edd9a443702f734e66f944c34e2`. It was last written on 17 September 2026 at 13:24 IST, before the build.
- Build: `uv run impact build`, run on 17 September 2026 (13:26 to 13:30 IST). Between the build and the tag, the only code changes were the restructure into subpackages (module renames), the M1 VWAP rounding tolerance, a CLI name-shadowing fix, and the D4 empty-bin guard. None of them affects build outputs. The event table is `parquet/event_table.parquet`.
- Every M0 to M4 gate passes (`impact check m0` to `m4`), except the exceptions recorded in section 7 with their reasons.

## 2. Primary cell

1. **Base cell.** `impact.analysis.estimators.primary_cell(event_table)` keeps events that meet all of the following:
   - buy stock-days (`side == 1`) dated 2013-04-01 to 2026-07-31, in the EQ series;
   - stock-day type I or R at the 80% threshold, with R events also passing the novelty filter;
   - backward-isolated, with no news and no calendar skip;
   - `LAST_t` not at a price band, previous unadjusted `LAST` at least Rs 10, and at least 70 trading days since listing;
   - `sigma`, `adv`, `bucket` and `g1` all present.

   Events are already restricted to in-scope stock-days outside the block and institutional windows.
2. **Common support.** `estimators.common_support` drops buckets that lack events of either type. It then keeps `x` inside the overlap of the two groups' 1st-to-99th percentile ranges.

## 3. Outcome and model

- **Outcome.** `y = g1_abn / sigma`, the overnight leg ln(O_{t+1} / LAST_t) minus its same-date bucket benchmark, divided by the ex-ante Garman-Klass sigma. For buys, ε = +1.
- **Censoring.** An event is upper-censored when `open_band_next == 1` and lower-censored when `open_band_next == -1`. A censored event is censored at its observed value. A band flag counts only when NSE's band-hit list (`bh` in the day's PR zip) lists that stock on that side (PLAN J6 as amended at M2).
- **Mean.** μ = θ_I · 1{I} · √x + θ_R · 1{R} · √x + λ_bucket, from `estimators.impact_design(cell, ("I", "R"))`.
- **Estimator.** `estimators.censored_t`: a Student-t likelihood with ν = 4 and scale estimated. The covariance is clustered two ways, by stock and by date.

## 4. Hypothesis and decision rule

- H0: θ_I = θ_R, tested with `Fit.wald` on the contrast (1, -1, 0, ...). Two-sided, α = 0.05.
- The allowed claim, if H0 is rejected, is: "the news content of the disclosure differs between institutional and reference buyers per unit of disclosed flow." The language limits in `PLAN.md` section 1 apply.
- **Power gate (M2).** If either group has fewer than 1,000 events in the base cell, this test is reported as descriptive. The headline then becomes D1 plus S2.

## 5. Secondary tests (Holm correction across S1 to S4 × groups)

- **S1:** information lower bound with a breakdown k.
  - For institutional events, y* = y − Y_UB · (√(x + k · F-hat) − √x), for k ∈ {1, 2, 3}.
  - Test H0: θ*_I − θ_R ≤ 0, one-sided.
  - F-hat is fitted point-in-time from run-starts before t.
  - Y_UB = max(1, the upper 95% bound of pooled day-0 Ŷ_obs).
- **S2:** retention tests against the LLOB curve with T = 1: K(5) = 0.213, K(20) = 0.110.
  - The legs are stacked. `id` and `g1` use the censored MLE; `d15` and `d620` use a Huber regression.
  - The Wald nulls are φ∞ = 0 and φ∞ = 2/3.
  - This runs only for groups whose placebo loadings are insignificant.
- **S3:** identity news on exact crosses: α_I − α_R by buyer type, with seller-type fixed effects.
- **S4:** prop inventory sign, H: b_g1(P) < 0.

## 6. Robustness (reported, not gated)

The ranked list in `PLAN.md` section 8, run as specified there.

## 7. Exceptions and gate record

- **Band flags (PLAN J6, amended at M2).** On 218 sampled days, the price-only band rule reached 99.4% recall but only 47.2% precision against NSE's `bh` list. From M2, every band flag requires `bh` confirmation.
- **Live-snapshot check (M0).** This check compares the evening `bulk.csv` with the API pull. It needs 5 trading days and ran on 1 before the freeze. It audits the source's revision behavior, not any input to the test. It is reported as incomplete.
- **Typing audit (M4): gate missed by one name.** The gate needs at least 95% agreement on 200 names. Each round drew names no earlier round had seen:

  | Round seed | Agreement |
  |---|---|
  | 20260914 | 90.0% |
  | 20260915 | 92.0% |
  | 20260916 | 94.5% |
  | 20260917 | 93.5% |
  | 20260918 (final) | 94.5% |

  - **How misses were fixed.** Misses in each round became rules (name patterns and PIT insider matching) or hand-dictionary entries, never per-event edits.
  - **Why round 5 is final.** The misses had become a long tail of one-off names, and more rounds would only chase them. Round 5 was declared final before it was drawn, and its score is reported as measured.
  - **Who judged.** The verdicts are the author's, partly from recall (marked in each sheet's `note` column). They are not verified against filings.
  - **Direction of the error.** In the final round, 7 of the 145 names typed R were institutions and 0 of the 32 names typed I were reference traders. Contamination therefore runs one way. It pulls θ-hat_R toward θ_I and shrinks θ_I − θ_R by roughly the institutional share of R events, about 5%. A rejection of H0 is conservative; a non-rejection is correspondingly weaker.
  - **Other misses in the final round.** The other 4 misses involve strategic holders. Two are promoters absent from the PIT data, and two are corporate or private-equity stakes typed I.
  - **Fixes after round 5.** These were applied without a new audit round:
    - glued fund names such as "FUNDLIMITED" and "A SERIES OF";
    - a normalization bug that made the PRIVATE EQUITY sponsor pattern dead;
    - dictionary entries.
- **Power gate (M2): failed.** The build of 17 September 2026 puts I 814 and R 785 events in the primary cell before common support, short of 1,000 per group. The primary test (section 4) is therefore reported as descriptive, and the headline is D1 plus S2, as pre-registered. The filters were not relaxed to reach the threshold.
- **Delivery data gap (M1).** NSE serves an Excel workbook instead of a CSV for `sec_bhavdata_full` on 8 August 2022, so delivered quantity is missing for that day only. Delivery feeds no confirmatory quantity.
- **VWAP overlap check (M1), tolerance corrected.** The check compares legacy value/volume with `AVG_PRICE`. It first required 0.1% relative agreement and failed on 5% of rows. Every one of those rows was a penny stock whose gap was within `AVG_PRICE`'s 2-decimal rounding (0.005), with identical volumes. The tolerance is now the larger of 0.1% and 0.005, and all rows pass.
- **Other gates.** All other M0 to M4 gates pass: manifest, bhavcopy overlap, delivery overlap, factor confirmation, pricing, Q/V, band-list coverage, the M3 replication, and delivery on churner-dominated days.

## 8. Change log

- **17 September 2026: frozen as `spec-v1`.**
  - The primary test is descriptive because the M2 power gate failed (section 7).
  - The D4 simulation ran before the freeze and uses no market data. With churners present, a free exponent is biased by about -0.44, which supports dropping it.
  - No outcome by trader type had been computed.
- **17 September 2026: implementation fix, tag `spec-v1.1`.** The first `impact report` run stopped inside S1, before writing any output.
  - **Cause.** 189 event stock-days have a zero median-volume ADV, which leaves x undefined. One such run-start made F-hat NaN for 5,379 events, 256 of them in the S1 cell, and the NaN outcomes broke the likelihood.
  - **Fix.** Confirmatory cells (`quality`) and F-hat run-starts now require ADV > 0.
  - **Effect.** The primary cell had no such events and is unchanged. The fix was diagnosed from forecast inputs only, and no outcome was read before this tag.
- **18 September 2026: provenance labelled and measured (no change to any test).** The hand dictionary now carries an `evidence` column: 26 of 29 entries are `unverified_recall`, the author's recollection rather than a source, and 3 are `name_inference`. `impact sensitivity` re-types every client-day with the dictionary reduced or removed and re-runs both contrasts. Under 1% of cell events touch a dictionary name; dropping it entirely moves v1 buys from z = 2.86 to 2.83 and leaves C2 sells a null (z = -1.20 to -0.89). Tagged `spec-v2.1`.
