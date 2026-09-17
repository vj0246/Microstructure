# Overnight Price Response to Disclosed Large Trades on NSE: v1 Plan

Design frozen 13 September 2026. It comes from an adversarial review of the original memo by four agents (identification referee, data-feasibility engineer, impact econometrician, scope-cutting PI) and a judge who ruled on every contested point (record in section 12). The confirmatory test is frozen again in `SPEC.md` at milestone M4, under a git tag, before any outcome is computed by trader type.

Evidence labels: [verified] opened or fetched this session; [computed] arithmetic on verified inputs; [simulated] from the econometrician's simulations; [recalled] not checked; [inference] reasoning.

## 1. Question and allowed claims

NSE publishes every bulk deal (client name, side, quantity, average price) after the close of the trade date. SEBI's circular of 14 January 2004, quoted in NSE/cmtr/4808, requires dissemination "on the same day after market hours". The live file is modified around 19:00 IST, and no public intraday channel reveals the client [verified]. So the return from the last trade of day t to the next open brackets the moment identity and size become public.

v1 asks three questions:

1. How far does the price move by the next open, per unit of disclosed net flow?
2. At the same flow, does that move differ between institutional buyers and unrecognizable directional buyers?
3. How much of the post-trade change persists beyond the inverse-square-root (LLOB) retention benchmark?

Allowed claims:
- "News content of the disclosure" (g1).
- "Differs by trader type."
- "Information lower bound under breakdown k" (S1).
- "Persistent component above the inverse-square-root benchmark, inclusive of endogenous continuation."
- Day 0 only: "conditional mean response on days with net disclosed flow of x ADV (impact plus selection)."

Forbidden claims:
- "Information" for total g1 or for any plateau.
- Any exponent claim, including "the square-root law holds (or fails) in India."
- Calling day 0 "anonymous." Brokers see names, and Indian front-running is documented.
- Calling day-0 Y "mechanical."
- Any causal effect of trader type.

What changed from the memo, and why:
- **Two-sample comparison (disclosed trades vs index trades): rejected.** It confounds anticipation, execution mechanism, stock composition and selection.
- **Day 0: descriptive only.** Traders condition on the intraday path: chasers load positive, liquidity providers negative, and the sign differs by type.
- **Free exponent: dropped.** Every estimator shares the day-0 simultaneity. The disclosure trigger is gross buy plus sell, so churners fill the low-net tail. Price-band censoring biases δ by about 0.1 [simulated].
- **Passive-flow calibration (memo Q3): moved to v2.** It measures a permanent demand elasticity, not transient impact. Free IWF and weight history exists only from January 2010 to April 2013 [verified].
- **Decisions requested in memo section 7.5:**
  - Scope: the identity-disclosure design replaces Q1 to Q3.
  - Start: 2010 for the panel, April 2013 for g1.
  - Venue: NSE only.
  - Timing: do not wait for the 30 September 2026 review.

## 2. Estimands

**Primary:** θ_I − θ_R, the overnight news per √x for institutional versus reference directional buys (section 7).

**Secondary:**
- **S1, information lower bound.** Subtract the no-arbitrage anticipation bound, UB_e = Y_UB·[√(x_e + k·F-hat_e) − √x_e], from institutional outcomes. Report the breakdown k. The valid bound is A_I − A_R ≤ UB_I; the difference of bounds is not conservative.
- **S2, retention.** Linear-restriction tests of φ∞ = 0 (pure mechanics) and φ∞ = 2/3 (fair pricing) against the LLOB curve with T = 1 day, K(τ) = √(1+τ) − √τ. K(5) = 0.213 and K(20) = 0.110 [computed]. Run only for groups whose placebo loadings are insignificant.
- **S3, cross identity news.** On exact negotiated crosses (net flow zero), estimate α_I − α_R by buyer type, with seller-type fixed effects.
- **S4, prop inventory sign.** H: b_g1(P) < 0, because disclosure forecasts the unwind of contrarian inventory.

**Descriptive:**
- **D1:** binned day-0 curve plus censored Ŷ_obs per group with δ = 1/2, and placebo loadings.
- **D2:** census covering:
  - events per year;
  - churn, cross and censored shares;
  - Q/ADV decades, pooled and within stock;
  - overnight variance share;
  - continuation hazards by type.
- **D3:** Rajvanshi (2021) replication, pooled only.
- **D4:** gross-trigger truncation simulation.

**Deliverables:**
- **Figure 1.** (A) Cumulative σ-unit legs from day −5 to +20 by group, with a disclosure marker. (B) The D1 curve with a slope-1/2 reference.
- **Table 1.** Stacked b(id, g1, d15, d620) by group with joint confidence intervals, the primary test row, the S1 breakdown k, and the S2 Wald tests.

## 3. Data sources

All free and all [verified] this session unless marked. Archive hosts need a browser User-Agent plus a Referer. `www.nseindia.com/api` needs the cookie from `/report-detail/display-bulk-and-block-deals`. Rate: at most 1 request per second, with backoff on 401/403/429/5xx.

| Source | Location | Depth | Quirks |
|---|---|---|---|
| Bulk, block, short-sale deals | `www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType={bulk_deals,block_deals,short_selling}&from=DD-MM-YYYY&to=DD-MM-YYYY&csv=true` | 2004+ | JSON mode silently stops at 70 rows, so use CSV only. Window at most 12 months (15 returns HTTP 500). BOM, space-padded quoted headers, Indian digit grouping, DD-MON-YYYY dates. No series, ISIN, broker or timestamp. |
| Live deals | `nsearchives.nseindia.com/content/equities/bulk.csv`, `block.csv` | today only | Overwritten daily; used for the M0 live check. |
| Legacy CM bhavcopy | `nsearchives.../content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip` | 1995 to 2024-07-05 | OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE, TOTTRDQTY, TOTTRDVAL. Trailing header comma. 2010 files lack TOTALTRADES and ISIN and use unpadded days. |
| UDiFF CM bhavcopy | `nsearchives.../content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` | 2024-01-01+ | 34 columns including ISIN, SctySrs, LastPric. |
| sec_bhavdata_full | `nsearchives.../products/content/sec_bhavdata_full_DDMMYYYY.csv` | about Oct 2019+ | AVG_PRICE, DELIV_QTY. Space-padded; "-" means missing. |
| MTO delivery | `nsearchives.../archives/equities/mto/MTO_DDMMYYYY.DAT` | 2002+ | 4 preamble lines; rows are `20,sr,SYMBOL,SERIES,traded,deliverable,pct`. |
| PR zip | `nsearchives.../archives/equities/bhavcopy/pr/PRDDMMYY.zip` | 2010+ | `bh` band-hit flags, `Bc` corporate actions, `Bm` board meetings, MCAP issue size (2024+), `ffix` weights (2010 to 2013-04). |
| Price bands | `eq_band_changes_DDMMYYYY.csv` (2010+), `sec_list_DDMMYYYY.csv` (2020+) | | Used to validate the censoring indicator. |
| Corporate actions | `api/corporates-corporateActions` | 2005+ | JSON: exDate, subject, isin, faceVal. |
| Board meetings | `api/corporate-board-meetings` | 2008+ | bm_date, bm_purpose, bm_timestamp. |
| Financial results | `api/corporates-financial-results` | 2012+ | broadCastDate to the second. |
| Insider trades (PIT) | `api/corporates-pit` | 2016 to 2026-03 | acqfromDt, acqtoDt, personCategory, acqMode. Endpoint returns nothing from June 2026. |
| Security master | `symbolchange.csv`, `namechange.csv`, `EQUITY_L.csv` | snapshots | Key on symbol chains with date ranges, not ISIN (ISIN changes on face-value splits [recalled]). |
| F&O universe | F&O bhavcopy, first trading day of each month | 2010+ | F&O names have dynamic bands, so they are treated as uncensored [recalled]. |

v1 needs no shares-outstanding series. MCAP issue size from 2024 on is used only to validate the disclosure trigger.

## 4. Samples and filters

- **Disclosed panel:** 2010-01-01 to 2026-07-31. The end date avoids the closing auction introduced on 3 August 2026 [verified secondary].
- **g1 primary window:** 2013-04-01 to 2026-07-31. From that date the next-day open is a call-auction price for all EQ stocks. Before it, only Nifty and Sensex names (from 2010-10-18) had an auction open; all others opened on the first continuous trade [verified].
- **Series and price:** EQ series only. Drop SM and ST; add BE and BZ back as robustness. Price at least Rs 10 on LAST_{t−1}.
- **Drop:**
  - periodic call auction scrips (if the list is unrecoverable, use the bottom 1% by trade count);
  - special sessions (Muhurat, weekend);
  - stocks with fewer than 70 trading days since listing.
- **News and calendar filters.** Drop an event if any of these holds:
  - a board meeting on t or t+1;
  - a results broadcast between 15:30 on t−1 and 09:00 on t+1 (timestamps from 2012);
  - an ex-date in [t, t+1];
  - an index effective day (last trading day of March or September, or an MSCI effective date).
- **Price bands:** keep names with bands of 5% or less and handle them through censoring. Dropping them is a robustness check only.

## 5. Variable definitions

- **Prices.** All prices are adjusted for splits and bonuses, point-in-time. LAST is the last continuous-session trade. C is the official close (the 15:00-15:30 VWAP before 3 August 2026). O is the open.
- **Unit of observation.** A stock-day t with net disclosed imbalance N_t = Σ_clients (B_c − S_c). A cross nets to zero automatically.
  - ε = sign(N_t).
  - x = |N_t| / ADV.
- **ADV.** Median daily volume over [t−60, t−6], excluding days with any disclosure in the stock. At least 30 valid days.
- **σ.** Garman-Klass: σ² = mean over [t−70, t−11] of 0.5·ln²(H/L) − (2 ln 2 − 1)·ln²(LAST/O). Exclude event days and band-hit days. At least 40 valid days.
- **Return legs.** Every leg is benchmark-adjusted, ε-signed and divided by σ.
  - ov_t = ln(O_t / LAST_{t−1}). Reported only: it mixes pre-open executions with evening news from t−1.
  - id_t = ln(LAST_t / O_t). The day-0 leg; descriptive.
  - g1 = ln(O_{t+1} / LAST_t). The primary estimand.
  - d15 = ln(LAST_{t+5} / O_{t+1}).
  - d620 = ln(LAST_{t+20} / LAST_{t+5}).
  - Placebos: r_{−1} = ln(LAST_{t−1} / LAST_{t−2}) and r[−5,−1] = ln(LAST_{t−1} / LAST_{t−6}).
  - Identity: ov_t + id_t = ln(LAST_t / LAST_{t−1}).
  - Reason for LAST: the official close averages prices before 15:30, so using it as the g1 baseline would leak late-day drift into g1.
- **Benchmark.**
  - Buckets are formed on the same date: quintiles of median traded value over [t−60, t−6] crossed with σ terciles.
  - Eligible stocks have no disclosure in [t−5, t+20].
  - The benchmark is the equal-weighted mean of each leg within the bucket.
- **Censoring indicator** (non-F&O names only).
  - A price is flagged when it equals round_tick(RP·(1 ± b)) within ±1 tick, for b ∈ {2, 5, 10, 20}%, where RP is the prior official close.
  - g1 is censored when O_{t+1} sits at a band.
  - Events with LAST_t at a band are excluded from the g1 primary: unfilled demand spills into the next open.
  - Validate against the `bh` flags and `sec_list`. If agreement falls below 95%, rebuild per-stock bands from `eq_band_changes`.
  - **Amended at M2 (15 September 2026).** On 218 sampled days the price rule reached 99.4% recall but only 47.2% precision against NSE's `bh` band-hit list. It also flags 2% and 5% moves of stocks whose band is wider. Rather than rebuild bands, every flag now requires NSE's own list: a price counts as at-band only when the `bh` file lists that stock and side for that day. This needs a PR zip for at least 99% of trading days (the M2 gate).
- **Isolation (backward-looking only).** No disclosure by the same client in the same stock during [t−20, t−1], and no disclosed flow in the stock by anyone during [t−5, t−1]. Forward isolation conditions on continuation, which responds to g1, so it appears only as a labeled diagnostic.
- **Crosses (exact rule, primary).**
  - Same stock-day, opposite sides, different normalized names.
  - Weighted-average prices equal to the paisa, equal quantity, matched greedily 1:1.
  - Measured prevalence is 2.5% to 7.3% of stock-days [verified].
  - Sensitivity rule: quantity within max(1 lot, 0.5%) and price within max(1 tick, 0.05%), with a placebo pairing across stock-days to estimate false matches.
- **Trader types.** A deterministic regex plus a hand dictionary, covering at least 90% of rupee value.
  - **I (institutional):** MF/AMC, insurer, pension or provident fund, bank, sovereign, FPI, AIF (tagged). Passive ETF and index-fund names carry a separate tag.
  - **R (reference):** individuals, HUF and unaffiliated private corporates. Directional only, and never disclosed in the prior 250 trading days (the novelty filter).
  - **P (churner):** defined by behavior. Client-day net/gross below 0.5, or a point-in-time history in which at least 50% of client-days over the prior 250 days were two-sided. Churner net flow counts in x but never defines a stock-day's type.
  - **S (strategic tag):** issuer-name token match or a PIT promoter-group name. Excluded from R and reported descriptively.
  - **Stock-day type:** the group supplying at least 80% of the event side's gross flow from directional, non-churner, non-cross clients. Otherwise the day is "mixed" and excluded from contrasts.
- **F-hat (S1 only).** The point-in-time mean of same-direction disclosed net flow by the same client in the same stock over t+1 to t+5, in ADV units. Estimated on run-starts before t in type × x-tercile cells, with an expanding window. Cells need at least 50 run-starts; otherwise use the type mean.

## 6. Estimation and inference

**Censored Student-t MLE with ν = 4.** For outcome y_e with group dummies D_ke and bucket fixed effects λ on common-support buckets:

    μ_e = Σ_k θ_k · D_ke · √x_e + λ_b(e)
    ℓ = Σ_uncensored [log f_ν((y_e − μ_e)/s) − log s]
      + Σ_upper log[1 − F_ν((y_e − μ_e)/s)]
      + Σ_lower log F_ν((y_e − μ_e)/s)

For a censored event the cap equals the observed value, so the likelihood needs only the censoring indicator.

- **Covariance.** Two-way clustered sandwich, V = H⁻¹(S_stock + S_date − S_stock×date)H⁻¹, built from event scores.
- **Stacked legs (S2).**
  - Censored MLE for id and g1; Huber regression for d15 and d620.
  - Joint covariance from the stacked clustered scores.
  - Linear Wald tests for the LLOB restrictions; φ-hat∞ by minimum distance, with a Fieller interval.
  - No control for subsequent disclosed flow: that flow is post-treatment.
- **S1.**
  - Bound: Y_UB = max(1, upper 95% bound of pooled day-0 Ŷ_obs).
  - Adjusted institutional outcome: y*_e = y_e − UB_e.
  - Test H0: θ*_I − θ_R ≤ 0, one-sided, for k ∈ {1, 2, 3}; report the breakdown k.
- **D1.** y0 = ε·abnormal id_t / σ. Binned means over 12 quantile bins with cluster-bootstrap confidence intervals, plus censored Ŷ_obs per group.
- **Multiple testing.** Holm correction across S1 to S4 × groups.

## 7. Pre-registered primary test

- **Cell.** Every event must meet all of the following:
  - directional buy stock-day inside the g1 window, EQ series;
  - stock-day type I or R at the 80% share threshold;
  - backward-isolated, not a cross, not flagged by the news filters;
  - LAST_t not at a band;
  - within common support on bucket and on the x range (1st to 99th percentile overlap).
- **Estimator.** The censored t-MLE of section 6, with θ_I and θ_R.
- **Hypothesis.** H0: θ_I = θ_R, two-sided, α = 0.05.
- **Power.** The minimum detectable effect is about 0.14σ with 1,000 events per group [computed; assumes an overnight variance share of 0.20, recalled; D2 measures it].
- **No peeking.** Nothing computes g1 by type before the `SPEC.md` git tag. The M3 replication is pooled only, so it cannot expose returns by investor category.

## 8. Robustness (ranked)

1. Official close as the g1 baseline (addresses bid-ask bounce at LAST).
2. ν estimated instead of fixed; uncensored OLS; censored events dropped.
3. Add pre-2013 opens.
4. Day-t controls (ε·id/σ, ln(LAST/p-bar)/σ), with the collider caveat stated.
5. Reference variants: no novelty filter; prop as the reference; individuals only; corporates only.
6. Cross tolerance rule; drop stock-days with any cross.
7. Add back BE and BZ; drop names with bands of 5% or less.
8. Delivery-backed stock-days only.
9. Exclude 2020-21; split 2013-19 against 2020-26.
10. Level model with x-bucket fixed effects; date fixed effects; stock-only clustering; pigeonhole bootstrap.

## 9. Milestones and kill gates

Any failed gate stops work until the cause is explained.

**M0: data freeze (up to 3 days).**
- **Work.**
  - Pull every section 3 source, including all deal years via `csv=true` and PIT for 2016 to 2026-03.
  - Store raw files write-once with a sha256 manifest.
  - Run a literature check for prior work on same-evening identity disclosure. If it exists, reposition v1 as replication plus extension.
- **Gates.**
  - Bulk rows for 2010-19 equal 76,784 and fall within ±10% of Rajvanshi's 81,506. Currently the gap is −5.8% [computed].
  - Each year re-pulled as two halves sums to the full-year pull.
  - API rows match the live `bulk.csv` on 5 separate days.
  - The PIT feed carries acquisition from/to dates (passed [verified]).

**M1: clean panel (1 week).**
- **Gates.**
  - Legacy and UDiFF bhavcopy agree exactly on 2024-01-01 to 2024-07-05.
  - MTO and `sec_bhavdata_full` delivery agree exactly on their overlap.
  - Legacy VWAP (TOTTRDVAL/TOTTRDQTY) is within 0.1% of AVG_PRICE.
  - At least 99.5% of bulk rows are priced on their date.
  - Net Q/V is at most 1 on every client-day (gross at most 2V).
  - Block-window duplicates are removed from the bulk file.
  - A corporate-action audit of 100 events is at least 98% correct.
  - Adjusted day-to-day moves above 50% are reviewed (the leg identity holds by construction, so it tests nothing).
  - A PREVCLOSE-continuity check finds no missing sessions.

**M2: census (1 week).**
- **Work.** Produce D2. Compute no returns by type.
- **Gates.**
  - The censoring indicator agrees at least 95% with `bh` and `sec_list`; otherwise rebuild bands.
  - n_I ≥ 1,000 and n_R ≥ 1,000 in the primary cell. Otherwise the primary becomes descriptive, and the headline becomes D1 plus S2.

**M3: replication (3 days).**
- **Work.** Replicate Rajvanshi on pooled buys and sells, 2010-19.
- **Gates.**
  - Day-0 abnormal return matches in sign and is within a factor of 2 in size.
  - Returns peak on day 0 and then decline.
  - A failure means a pipeline bug.

**M4: typing and freeze (1 week).**
- **Work.** Fit F-hat point-in-time. Compute placebo loadings per group. Freeze `SPEC.md` under a git tag.
- **Gates.**
  - A 200-name typing audit is at least 95% correct.
  - Delivery on churner-dominated days is below the bucket median.

**M5: confirmatory (1 to 2 weeks).**
- **Work.** The primary test, S1 to S4, robustness, D4, and the report.

## 10. Code layout

- **Tooling.** Python 3.12 via uv. Dependencies are added as each milestone needs them: httpx, pydantic-settings, polars, numpy, scipy, matplotlib, pyfixest (for OLS robustness and the replication), pytest.
- **Data location.** Data lives outside the repository in `IMPACT_DATA_DIR` (default `~/data/microstructure`), which keeps gigabytes off OneDrive sync.

```
pyproject.toml  README.md  docs/PLAN.md  docs/SPEC.md (M4)  scripts/run_pipeline.cmd
src/impact/
  cli.py        fetch | check | build | simulate | report
  config.py     settings: paths, rate limit (IMPACT_* env vars)
  pipeline.py   memory-bounded raw -> parquet build (prices by year, panel in symbol chunks)
  gates.py      milestone gates (M0 to M4)
  ingest/       nse_client.py (session, cookie, throttle, backoff, URL builders, write-once raw store + manifest)
                readers.py (raw -> typed polars frames per source and era)
  clean/        symbols.py (symbol chains, date ranges), adjustment.py (split and bonus factors, point-in-time),
                panel.py (legs, ADV, sigma, buckets and benchmarks, censoring flags)
  flows/        disclosures.py (stock-day netting, crosses, type shares, isolation, filters),
                trader_types.py + client_types.csv (typing: regex, PIT insiders, hand dictionary)
  analysis/     estimators.py (censored t-MLE, sandwich, stacked legs, restrictions, bound),
                truncation_sim.py (D4), report.py (Figure 1, Table 1)
tests/
```

**Tests that matter:**
- Parser golden rows per era: Indian digit grouping, BOM and padded headers.
- Netting and exact crosses.
- Point-in-time perturbation of ADV, σ, F-hat, isolation and the churner flag.
- Continuity across a synthetic 1:10 split.
- The leg identity.
- The censored MLE recovers θ under censoring.
- The sandwich matches pyfixest on a toy OLS.
- The simulation recovers δ without truncation.
- Client retry, cookie and throttle behavior against a mock transport.

**Not built:** a database server, an orchestrator, a dashboard or API, an LLM classifier, notebooks, a source-abstraction layer, BSE data, or a shares-outstanding reconstruction.

## 11. v2

- A free δ from PIT metaorders (low Q/V, known duration).
- SAST Regulation 29 episodes.
- Q3 elasticity: a regression discontinuity at the AMFI rank-100 and rank-250 cutoffs, index methodology changes, and `ffix` for 2010-13.
- MSCI events.
- The closing auction after 3 August 2026, as a holdout.
- A plateau horse race: Q/S against σ√(Q/V).
- Float reconstruction and XBRL promoter data.
- BSE data.
- IPO anchor unlocks.
- Block deals after December 2025, as an informed-seller sample.

## 12. Ruling record

| # | Point | Ruling | Reason |
|---|---|---|---|
| J1 | Reference group | Directional individuals, HUF and unaffiliated corporates, with a novelty filter; promoters excluded. Prop moves to S4. | Prop net flow is contrarian inventory, so the test would reject under pure mechanics. |
| J2 | Isolation | Backward-only. | Condition only on information dated before the disclosure. |
| J3 | Anticipation | Primary is plain equality, labeled "total news content"; the bound becomes S1. | A confirmatory test cannot rest on unobservable, below-threshold continuation. |
| J4 | Free δ | Dropped from v1. | Rebuilding bands fixes censoring, not simultaneity. |
| J5 | Bands of 5% or less | Kept, with censoring. | Dropping them deletes the operator flow. |
| J6 | Censoring | Indicator-only censored t-MLE, validated against band files. | The likelihood needs only which observations are censored. |
| J7 | Decay | LLOB curve with T = 1 tested as linear restrictions; no subsequent-flow control. | Avoids ratio statistics with Cauchy tails; that control would be post-treatment. |
| J8 | Return legs | LAST as the g1 baseline; open-to-last as the day-0 leg. | The official close is a VWAP that includes pre-disclosure drift. |
| J9 | g1 window | From 2013-04-01. | A first continuous trade is a single noisy print, not a clearing price. |
| J10 | Filters | As in section 4. | Removes confounding news and mechanical calendar events. |
| J11 | PIT | Pulled in M0; analysis in v2. | The endpoint is already dark, so delay risks permanent loss. |
| J12 | Crosses | Exact rule primary; tolerance rule as sensitivity. | Precision beats recall for the control group. |
| J13 | Taxonomy | I / R / P / S as in section 5. | Types defined by behavior are harder to game than names. |
| J14 | Remaining risks | Bid-ask bounce at LAST, composition, singleton date FE, bulk and block double counting, pre-registration leakage, survivorship. | Each is mitigated in sections 4 to 9. |
