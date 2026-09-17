"""Gross-trigger truncation simulation (PLAN D4): what disclosure truncation, price bands, churners and
a liquidity-linked impact scale do to the binned day-0 curve and to a free-exponent fit, when the true
law is r = Y sigma (|net| / ADV)^delta plus heavy-tailed noise."""

from dataclasses import dataclass

import numpy as np
import polars as pl

from impact.analysis import estimators

MEDIAN_TURNOVER = 0.0025  # ADV / shares outstanding [inference, round-1 econometrician]
TRIGGER = 0.005  # disclosure: gross buy + sell per client-day >= 0.5% of shares


@dataclass(frozen=True)
class Scenario:
    delta: float = 0.5
    gamma: float = 0.0  # impact scale ~ turnover^gamma
    trigger: bool = True
    bands: bool = True  # 5/10/20% caps in a 20/20/60 mix of stocks
    churn: float = 0.5  # share of candidate client-days that are round trips with contrarian impact
    y0: float = 0.7
    stocks: int = 1500
    per_stock: int = 40


# One distortion at a time, then together: the factorial the D4 table reports.
SCENARIOS = {
    "clean": Scenario(trigger=False, bands=False, churn=0.0),
    "trigger": Scenario(bands=False, churn=0.0),
    "bands": Scenario(trigger=False, churn=0.0),
    "trigger+churn": Scenario(bands=False),
    "all": Scenario(),
    "all+liquidity": Scenario(gamma=-0.25),  # impact scale falls with turnover [inference]
}


def draw(s: Scenario, seed: int) -> pl.DataFrame:
    """Observed buy client-days: x = |net| / ADV, y = return / sigma, and band-censoring flags."""
    rng = np.random.default_rng(seed)
    n = s.stocks * s.per_stock
    stock = np.repeat(np.arange(s.stocks), s.per_stock)
    turnover = np.exp(rng.normal(np.log(MEDIAN_TURNOVER), 1.0, s.stocks))[stock]
    sigma = np.exp(rng.normal(np.log(0.025), 0.4, s.stocks))[stock]
    scale = s.y0 * (turnover / MEDIAN_TURNOVER) ** s.gamma * np.exp(0.3 * rng.normal(size=s.stocks))[stock]
    net = 0.0005 * (rng.pareto(1.5, n) + 1)  # |net| / shares outstanding
    churner = rng.random(n) < s.churn
    gross = net + np.where(churner, 2 * net * np.exp(rng.normal(2.0, 0.5, n)), 0.0)  # round trips ~7x net
    x = net / turnover
    impact = scale * sigma * x**s.delta * np.where(churner, -0.3, 1.0)
    ret = impact + 1.5 * sigma * rng.standard_t(estimators.NU, n)
    cap = rng.choice([0.05, 0.10, 0.20], s.stocks, p=[0.2, 0.2, 0.6])[stock] if s.bands else np.full(n, np.inf)
    seen = gross >= TRIGGER if s.trigger else np.ones(n, dtype=bool)
    frame = pl.DataFrame(
        {"stock": stock, "x": x, "y": np.clip(ret, -cap, cap) / sigma, "upper": ret >= cap, "lower": ret <= -cap}
    )
    return frame.filter(pl.Series(seen))


def binned_exponent(frame: pl.DataFrame, bins: int = 12) -> float | None:
    """Slope of log bin-mean y on log bin-median x over quantile bins with a positive mean: the reading a
    plot of the binned curve invites. None when fewer than 3 bins have a positive mean (no slope to read)."""
    binned = (
        frame.with_columns(bin=pl.col("x").qcut(bins, labels=[str(i) for i in range(bins)]))
        .group_by("bin")
        .agg(pl.col("x").median(), pl.col("y").mean())
        .filter(pl.col("y") > 0)
    )
    if binned.height < 3:
        return None
    slope, _ = np.polyfit(np.log(binned["x"].to_numpy()), np.log(binned["y"].to_numpy()), 1)
    return float(slope)


def profile_exponent(frame: pl.DataFrame, grid: np.ndarray) -> float:
    """delta maximizing the censored t likelihood of y = Y x^delta over `grid`."""
    y, x = frame["y"].to_numpy(), frame["x"].to_numpy()
    upper, lower, stocks = frame["upper"].to_numpy(), frame["lower"].to_numpy(), frame["stock"].to_numpy()
    fits = [estimators.censored_t(y, (x**d)[:, None], ("Y",), upper, lower, stocks) for d in grid]
    return float(grid[int(np.argmax([f.loglik for f in fits]))])


def run(scenarios: dict[str, Scenario], seeds: range, grid: np.ndarray = np.arange(0.0, 1.01, 0.02)) -> pl.DataFrame:
    """Exponent estimates per scenario and seed; compare with each scenario's true delta."""
    rows = []
    for name, s in scenarios.items():
        for seed in seeds:
            frame = draw(s, seed)
            rows.append((name, seed, s.delta, frame.height, binned_exponent(frame), profile_exponent(frame, grid)))
    schema = {"scenario": pl.String, "seed": pl.Int64, "delta": pl.Float64, "events": pl.Int64, "binned": pl.Float64, "profile": pl.Float64}
    return pl.DataFrame(rows, schema=schema, orient="row")


def summary(runs: pl.DataFrame) -> pl.DataFrame:
    """Per scenario: mean events, and the bias and spread of each exponent estimator across seeds (the
    binned estimator over the seeds where it exists, counted in binned_seeds)."""
    return runs.group_by("scenario", maintain_order=True).agg(
        pl.col("delta").first(),
        pl.col("events").mean().round(0),
        pl.col("binned").is_not_null().sum().alias("binned_seeds"),
        *[(pl.col(e) - pl.col("delta")).mean().round(3).alias(f"{e}_bias") for e in ("binned", "profile")],
        *[pl.col(e).std().round(3).alias(f"{e}_sd") for e in ("binned", "profile")],
    )
