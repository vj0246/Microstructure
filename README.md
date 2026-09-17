# What Does a Disclosed Large Trade Tell the Market? NSE Bulk Deals, 2010 to 2026

NSE publishes every bulk deal after the close: client name, side, quantity and average price. SEBI requires this "on the same day after market hours", and the file appears around 19:00 IST. Before that, no public channel reveals who traded.

The return from the last trade of day t to the next morning's call-auction open therefore brackets one moment: when the trader's identity and size become public. This project measures how much the price moves at that moment per unit of disclosed flow, and whether the move depends on who the buyer is:

- **Institutional (I):** mutual funds, insurers, pension and sovereign funds, foreign portfolio investors.
- **Reference (R):** individuals, HUFs, unaffiliated corporates.

The design was argued out by four adversarial reviewers and settled by a judge ([docs/PLAN.md](docs/PLAN.md)). The confirmatory test was pre-registered and frozen under the git tag `spec-v1` ([docs/SPEC.md](docs/SPEC.md)) before any outcome was computed by trader type.

## Identification

| Leg | Definition | Role |
|---|---|---|
| `g1` | ln(O_{t+1} / LAST_t) | **Causal leg.** It spans only the disclosure. |
| `id` | ln(LAST_t / O_t) | Day 0. Descriptive only: traders condition on the intraday path. |
| `ov` | ln(O_t / LAST_{t-1}) | Pre-trade overnight. |
| `d15`, `d620` | Open t+1 to day t+5, then t+5 to t+20 | Decay, for the retention tests. |
| `r_m1`, `r_m5` | Returns over t-2 to t-1 and t-6 to t-1 | Placebo loadings. Run-up means the group is anticipated. |

Choices behind the legs:
- **Baseline is LAST, not the official close.** Before 3 August 2026 NSE's close is the 15:00 to 15:30 VWAP, which averages over pre-disclosure prices. `g1c` (the official-close baseline) is kept as robustness check 1.
- **Window starts 1 April 2013.** From that date the next-day open is a call-auction price for every EQ stock.
- **Window ends 31 July 2026,** before the closing auction arrives on 3 August 2026.
- **All legs are abnormal.** Each is the event's leg minus the same-date mean leg of non-event stock-days in the same bucket, divided by an ex-ante Garman-Klass σ.

## Data

All data comes from NSE public archives and APIs. There are 14,940 raw files in a write-once store. Each file is sha256-hashed in `manifest.jsonl`; the manifest's own hash is `d7873714...34e2`.

| Source | Coverage | Used for |
|---|---|---|
| Bulk and block deals (CSV API, 12-month windows) | 2004 onward | Client-day flows. JSON mode silently truncates at 70 rows. |
| Legacy bhavcopy | 2009 to 5 Jul 2024 | OHLC, LAST, volume, value, ISIN |
| UDiFF bhavcopy | 1 Jan 2024 onward | The same, after the format change. It matches legacy exactly on all 276,472 overlap rows. |
| MTO | to Jun 2020 | Delivered quantity |
| sec_bhavdata_full | Oct 2019 onward | Delivered quantity and VWAP. Matches MTO exactly on the overlap. |
| PR zips (`bh` band-hit list) | 2010 onward, 100% of 4,143 trading days | Price-band censoring |
| PIT insider filings | 2016 to Mar 2026 (337,836 records) | Promoter, director and KMP identities |
| Corporate actions, board meetings, results | 2005 onward | Split and bonus factors, news filters |

## Pipeline

1. **Symbols.** Symbol-change chains are resolved to one canonical symbol per security. The resolution is date-aware, so reused tickers are handled.
2. **Adjustment.** NSE's legacy PREVCLOSE is *not* adjusted on ex-dates.
   - Split and bonus factors are parsed from corporate-action subject text, including abbreviated and glued forms.
   - A factor with |ln f| ≥ 0.3 is applied only when the ex-date price jump confirms it: 1,063 of 1,067 (99.6%) are confirmed.
   - For arrangements and demergers, the observed gap is used instead.
3. **Flows.**
   - **Block-window netting.** The bulk file aggregates a client's day across the normal, block (BL) and institutional (IL) windows. Block-file legs are subtracted by client, side and stock-day, and the remainder is repriced. After this, 0 of 102,982 client-days exceed daily volume.
   - **Crosses.** Exact 1:1 buyer-seller quantity matches are flagged.
   - **Scope.** SME, depository-receipt and fund-unit (INF ISIN) deals are flagged out of scope.
4. **Panel.** Built on the market calendar (weekend Muhurat and special sessions included), in 200-symbol chunks so memory stays bounded.
   - **ADV:** median volume over [t-60, t-6], with at least 30 days and disclosure days excluded.
   - **σ:** Garman-Klass variance over [t-70, t-11], with at least 40 days and band-hit days excluded.
   - **Benchmark days:** no disclosure anywhere in [t-5, t+20].
   - **Buckets:** 5 turnover quintiles × 3 σ terciles, with cut points recomputed each year.
5. **Price-band censoring.** A price rule alone flagged band hits with 99.4% recall but only 47.2% precision against NSE's own list, because it also flags 2% and 5% moves of wider-band stocks. Every band flag therefore requires the stock and side to appear in that day's `bh` file.
   - `LAST_t` at a band: the event is excluded, because unfilled demand spills into the open.
   - Next open at a band: `g1` is censored at its observed value.
6. **Event flags.** All flags are backward-looking only.
   - **Isolated:** the same client did not disclose in the stock over the previous 20 trading days, and nobody disclosed in the stock over the previous 5.
   - **Novel:** the client did not disclose in any stock over the previous 250 days. This applies to R only.
   - **News:** a board meeting or ex-date on t or t+1, or a results broadcast between 15:30 on t-1 and 09:00 on t+1.

## Trader types

The rules are deterministic, and the first match wins:

1. hand dictionary (`client_types.csv`, with its evidence noted);
2. prop-desk name patterns (P);
3. venture and private-equity sponsors (S);
4. regulated pools (I): sovereign, pension, insurer, mutual fund or trustee, endowment, AIF, bank or ARC;
5. strategic for this stock (S): a distinctive issuer token in the client name, or a PIT insider of the stock. Insiders are matched on the full name in any word order, on first plus last name, or on a surname shared with an insider when fewer than 30 client names carry that surname;
6. investment managers, FPI and fund name patterns (I);
7. behavioural churner (P): at least 5 disclosed client-days in the prior 365 days, at least 50% of them two-sided;
8. otherwise R (individual, HUF, trust or corporate).

**Stock-day type.** A stock-day takes the type that supplies at least 80% of the event side's directional (|net| ≥ gross/2), non-churner, non-cross quantity.

**Churner validation (M4).** On 10,331 churner-dominated stock-days, delivery share runs a median 22.8 points below the bucket-year median, and 89% of those days sit below it. Round trips do not take delivery, so this is what churning should look like.

**Typing audit.** Each round drew 200 names that no earlier round had seen, and the verdicts were recorded before scoring.

| Round seed | 20260914 | 20260915 | 20260916 | 20260917 | 20260918 (final) |
|---|---|---|---|---|---|
| Agreement | 90.0% | 92.0% | 94.5% | 93.5% | 94.5% |

The gate is 95%, so the final round misses it by one name. The errors run one way: 7 of 145 R-typed names are really institutions, and no I-typed name is really R. That contamination pulls θ_R toward θ_I by about 5%, so any rejection of H0 is conservative.

## Estimation

- **Primary model.** y = g1_abn / σ = θ_I·1{I}·√x + θ_R·1{R}·√x + λ_bucket + ε.
  - x = |net disclosed flow| / ADV, in split-adjusted shares.
  - ε follows a Student-t with ν = 4 and estimated scale. Upper and lower censoring comes from the confirmed band flags.
  - The fit is maximum likelihood (BFGS). A precision-loss exit is accepted only when max|score|/n ≤ 1e-6.
- **Inference.**
  - Two-way (stock, date) cluster-robust sandwich covariance (Cameron-Gelbach-Miller), built from per-event scores, with eigenvalues clipped to keep it positive semi-definite.
  - Cross-checked against pyfixest on OLS.
- **Primary cell.** Buys, 2013-04 to 2026-07, EQ series, backward-isolated, no news, `LAST_t` off the band, previous unadjusted price at least Rs 10, listed at least 70 days.
  - Common support: buckets holding both groups, with x inside the overlap of each group's 1st-to-99th percentile range.
- **Hypothesis.** H0: θ_I = θ_R, two-sided Wald test, α = 0.05.
- **Secondary tests** (Holm correction across the family):
  - **S1, information bound.** Institutional runs continue, so part of `g1` could be anticipated continuation. Each institutional outcome is reduced by Y_UB·(√(x + k·F-hat) − √x), where F-hat is fitted point-in-time from run-starts before t, and Y_UB = max(1, the upper 95% bound of the pooled day-0 scale). The test is θ*_I − θ_R > 0 for k = 1, 2, 3, and the breakdown k is reported.
  - **S2, retention.** The id, g1, d15 and d620 legs are stacked (censored t for id and g1, Huber with c = 1.345 for decay). The test is against the latent-order-book decay K(τ) = √(1+τ) − √τ, with K(5) = 0.213 and K(20) = 0.110; Wald nulls are φ∞ = 0 and φ∞ = 2/3. It runs only for groups whose placebo loadings are insignificant.
  - **S3, identity news on exact crosses.** α_I − α_R by buyer type, with seller-type fixed effects.
  - **S4, prop inventory.** b_g1(P) < 0.

## Gate record

| Gate | Result |
|---|---|
| **M0: raw store** | Manifest re-hash is clean. Bulk rows for 2010 to 2019 number 76,784, which is −5.8% against Rajvanshi (2021). The live-snapshot check ran on 1 of the 5 days it needs (exception). |
| **M1: clean panel** | All pass: weekend sessions, factor confirmation, 100% of in-scope client-days priced, Q/V ≤ 1, bhavcopy and delivery overlaps, VWAP. |
| **M2: census and power** | Band lists cover 100% of days. Power gate **failed**: the primary cell has I 814 and R 785, against 1,000 needed per group. By pre-registration the primary is therefore descriptive, and the headline is D1 plus S2. |
| **M3: Rajvanshi replication** (pooled) | Buy day-0 AR is +2.46% against +1.56% for sells, peaking on day 0 and reversing over days +1 to +7. |
| **M4: typing** | Delivery gate passes. Typing audit 94.5% (exception above). |

## Census (D2)

- **Isolation and censoring.** 33.5% of event stock-days are backward-isolated. 9.8% have the next open at a band (censored `g1`), and 21.2% have `LAST_t` at a band.
- **Churners.** They supply 52% of in-scope client-days, because the disclosure trigger is *gross* buy plus sell (at least 0.5% of shares).
- **Size range.** Q/ADV spans 3.7 decades pooled (5th to 95th percentile), with a median within-stock sd of 0.87 decades.
- **Overnight variance.** The overnight leg carries 27% of daily return variance for the median stock.
- **Continuation.** The share of events followed by a same-side disclosure within 5 days, by type:

  | Type | I | P | R | S |
  |---|---|---|---|---|
  | 5-day continuation | 9.5% | 9.6% | 11.1% | 22.9% |

## Results (M5, run under `spec-v1.1`)

![Figure 1](docs/figure1.png)

After common support, the primary cell holds **791 institutional and 751 reference buys**. Both groups are below the 1,000 pre-registered as the power gate, so every number here is **descriptive**.

| Quantity | Estimate |
|---|---|
| θ_I, overnight abnormal return per √(Q/ADV), in σ units | **0.061** (se 0.010) |
| θ_R | **0.022** (se 0.014) |
| θ_I − θ_R | z = 2.86, p = 0.004 |
| D1: pooled day-0 slope Y_obs (within bucket) | −0.12 (se 0.03) |
| S1: information bound, k = 1, 2, 3 | z = −11.4, −13.7, −14.0; breakdown at k = 1 |
| Placebo run-up r_m1 | I: z = −2.06 (p = 0.04). R: z = −0.55 |
| S2: R retention (mechanical and fair, τ = 5 and 20) | all \|z\| < 0.7 |
| S3: identity news on exact crosses, α_I − α_R | z = 1.03, p = 0.30 |
| S4: churner-only flow, θ_P | +0.011 (se 0.009), one-sided p = 0.89 |
| Holm (S1 to S4) | nothing significant |

**What the numbers say.**
- **Size of the effect.** An institutional buy of one day's volume, in a stock with 2.5% daily σ, is followed by an overnight abnormal return of about **15 bp**. A reference buy of the same size is followed by about **5 bp**.
- **Institutional path.** Institutions buy after small declines, and on days when the price falls more the larger their buy is relative to ADV. The price then rebounds overnight, right after the disclosure, and keeps partly recovering to t+20, where the confidence interval includes zero.
- **Reference path.** Reference buys drift up slightly on day 0 and overnight, then give it back by t+20.
- **Day-0 level versus slope.** The day-0 *level* for disclosed buys is about +1σ. The within-bucket *slope* on √x is negative, which points to selection (buying into weakness, and at-band days are excluded) rather than a price-impact law.
- **Why S1 is uninformative.** The bound caps anticipated continuation at Y_UB = 1σ per √x, because the observed day-0 slope is negative and the floor applies. That cap is about 16 times θ_I, so subtracting even one unit of expected continuation removes the premium. The test cannot separate "news about the institution" from "the market pricing the institution's future buying".
- **Why S2 covers R only.** Institutions load on the r_m1 placebo, so S2 was run for R only, where it has no power.

**Robustness** (PLAN section 8), for the contrast θ_I − θ_R:

| Variant | n | θ_I | θ_R | z |
|---|---|---|---|---|
| Primary (censored t, ν = 4, stock × date clusters) | 1,542 | 0.061 | 0.022 | 2.86 |
| ν profiled (best ν = 2) | 1,542 | 0.057 | 0.013 | 3.38 |
| Censored events dropped | 1,533 | 0.060 | 0.020 | 2.96 |
| Day-0 control (collider caveat) | 1,542 | 0.055 | 0.022 | 2.39 |
| Excluding 2020-21 | 1,319 | 0.059 | 0.023 | 2.47 |
| 2013-19 only | 726 | 0.065 | 0.016 | 2.70 |
| Stock-only clustering | 1,542 | 0.061 | 0.022 | 2.90 |
| Official close (VWAP) baseline | 1,542 | 0.054 | 0.029 | 1.60 |
| 2020-26 only | 816 | 0.052 | 0.018 | 1.38 |
| Uncensored OLS | 1,542 | 0.052 | 0.040 | 0.67 |

The gap survives:
- heavier tails;
- dropping censored events;
- conditioning on the day-0 move, so it is not simply an overnight reversal of the day's decline;
- dropping the COVID years;
- clustering by stock only.

It weakens in three variants:
- **VWAP-close baseline.** Part of the gap may be bounce from a LAST printed at the bid.
- **2020-26.** θ_I barely moves, but the gap loses significance on fewer events.
- **OLS.** A few heavy-tailed reference outliers dominate the least-squares fit.

Robustness items not run: 3 and 5 to 8, which need cells the event table does not carry, plus date fixed effects and the pigeonhole bootstrap from item 10. The reasons are listed in `results.json`.

**Bottom line.** Descriptively, the overnight return after a disclosed buy is about three times as large per unit of √size when the buyer is an institution. The study cannot say whether that premium is news about fundamentals or the price of expected continued institutional buying. The sample is underpowered, and the type labels are about 94.5% accurate, with the errors biasing toward no difference.

## D4: why no free impact exponent

This is a simulated panel of 1,500 stocks with a true law r = Y·σ·(|net|/ADV)^0.5 plus t-noise, run over 10 seeds per scenario. Profile bias is for the censored-t fit; the binned fit is the slope of log mean response on log x.

| Scenario | Profile bias | Binned bias |
|---|---|---|
| Clean | −0.002 | +0.038 |
| Gross disclosure trigger | −0.006 | +0.029 |
| Price bands (censored) | −0.008 | +0.030 |
| Trigger + 50% churners | **−0.438** | undefined (no positive bins) |
| All distortions | **−0.442** | undefined |
| All + liquidity-linked impact | −0.260 | undefined |

With churners (52% of real client-days), a fitted exponent collapses toward zero. The model therefore fixes δ = 1/2, and churners are excluded when typing a stock-day. The churners' contrarian impact is an assumption of the simulation.

## Limitations

- The typing audit misses its 95% gate by one name, and the verdicts partly rest on the author's recall.
- The PIT data lacks some promoters (for example, the Kaynes founder), so some strategic sellers are typed R.
- The primary test is underpowered: fewer than 1,000 events per group.
- The live-snapshot revision check covers 1 of the 5 trading days it needs.
- Delivery data is missing for 8 August 2022, when NSE serves an Excel file under the CSV name.

## Layout

```
src/impact/
  cli.py          fetch | check | build | simulate | report
  config.py       IMPACT_* settings
  pipeline.py     raw -> parquet (prices by year, flows, panel chunks, benchmarks, event tables)
  gates.py        milestone gates M0 to M4
  ingest/         nse_client.py (throttle, cookie bootstrap, backoff, write-once store), readers.py
  clean/          symbols.py, adjustment.py, panel.py
  flows/          disclosures.py, trader_types.py, client_types.csv
  analysis/       estimators.py, truncation_sim.py, report.py
docs/             PLAN.md (design and rulings), SPEC.md (pre-registration), figure1.png
scripts/          run_pipeline.cmd (resumable pull, build, simulation)
tests/            one file per module (100 tests)
```

Python 3.12, uv, polars, httpx, numpy, scipy, matplotlib. Data lives outside the repo in `IMPACT_DATA_DIR`. Entry points: `uv run impact build`, `uv run impact check m0..m4`, `uv run impact simulate`, `uv run impact report` (the last refuses to run without the `spec-v1` tag).
