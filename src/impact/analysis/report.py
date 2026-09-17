"""Confirmatory run (PLAN M5) and its outputs: results.json, table1.csv and figure1.png.

`run` refuses to start until docs/SPEC.md is frozen under the git tag spec-v1, so nothing computes the
overnight leg by trader type before the freeze."""

import json
import subprocess
from pathlib import Path

import matplotlib
import matplotlib.ticker
import numpy as np
import polars as pl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set before pyplot loads)

from impact.analysis import estimators  # noqa: E402
from impact.clean import panel  # noqa: E402
from impact.flows import disclosures  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
SPEC_TAG = "spec-v1"
GROUPS = ("I", "R")
PATH_LEGS = ("r_m5", "ov", "id", "g1", "d15", "d620")
PATH_LABELS = ("t-5", "t-1 last", "t open", "t last", "t+1 open", "t+5", "t+20")
# Reference palette of the dataviz method (light surface): categorical slots 1 and 2, chart chrome.
SERIES = {"I": "#2a78d6", "R": "#eb6834"}
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
GROUP_NAMES = {"I": "Institutional", "R": "Reference"}


def require_spec(repo: Path = REPO) -> None:
    tags = subprocess.run(["git", "tag", "--list", SPEC_TAG], cwd=repo, capture_output=True, text=True, check=True)
    if SPEC_TAG not in tags.stdout.split():
        raise SystemExit(f"SPEC.md is not frozen: create tag {SPEC_TAG} first; overnight legs by type stay uncomputed")


def _test(z: float, p: float) -> dict[str, float]:
    return {"z": z, "p": p}


def _path(fit: estimators.Fit, group: str) -> dict[str, list[float]]:
    """Cumulative sigma-unit price path per sqrt(x), anchored at LAST_{t-1}, with standard errors."""
    index = {name: i for i, name in enumerate(fit.names)}

    def unit(leg: str) -> np.ndarray:
        vector = np.zeros(len(fit.names))
        vector[index[f"{leg}:theta_{group}"]] = 1.0
        return vector

    zero = np.zeros(len(fit.names))
    points, running = [-unit("r_m5"), zero], zero
    for leg in PATH_LEGS[1:]:
        running = running + unit(leg)
        points.append(running)
    return {
        "values": [float(c @ fit.coef) for c in points],
        "se": [float(np.sqrt(c @ fit.vcov @ c)) for c in points],
    }


def binned_day0(cell: pl.DataFrame, bins: int = 12, reps: int = 200, seed: int = 0) -> list[dict[str, float | str]]:
    """PLAN D1 curve: mean day-0 response (side * id_abn / sigma) in pooled x-quantile bins by group, with
    95% intervals from a bootstrap that resamples stocks."""
    frame = cell.with_columns(
        y=pl.col("side") * pl.col("id_abn") / pl.col("sigma"),
        bin=pl.col("x").qcut(bins, labels=[str(i) for i in range(bins)]).cast(pl.Int32),
    )
    stocks = frame["symbol"].unique().sort()
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(reps):
        weights = pl.DataFrame({"symbol": rng.choice(stocks.to_numpy(), stocks.len())}).group_by("symbol").len()
        weighted = frame.join(weights, on="symbol").group_by("stock_type", "bin").agg(
            mean=(pl.col("y") * pl.col("len")).sum() / pl.col("len").sum()
        )
        draws.append(weighted)
    spread = pl.concat(draws).group_by("stock_type", "bin").agg(
        lo=pl.col("mean").quantile(0.025), hi=pl.col("mean").quantile(0.975)
    )
    point = frame.group_by("stock_type", "bin").agg(x=pl.col("x").median(), mean=pl.col("y").mean())
    rows = point.join(spread, on=["stock_type", "bin"]).sort("stock_type", "bin")
    return rows.rename({"stock_type": "group"}).drop("bin").to_dicts()


def run(out: Path, repo: Path = REPO) -> dict:
    """Primary test, D1, S1 to S4 with Holm, and the figure inputs, as a JSON-ready dict."""
    require_spec(repo)
    table = pl.read_parquet(out / "event_table.parquet")
    cell = estimators.common_support(estimators.primary_cell(table))
    counts = {g: cell.filter(pl.col("stock_type") == g).height for g in GROUPS}
    X, names = estimators.impact_design(cell, GROUPS)
    band = cell["open_band_next"].to_numpy()
    y = cell["g1_abn"].to_numpy() / cell["sigma"].to_numpy()
    primary = estimators.censored_t(y, X, names, band > 0, band < 0, cell["symbol"].to_numpy(), cell["date"].to_numpy())
    z, p = primary.wald(np.eye(len(primary.names))[0] - np.eye(len(primary.names))[1])

    day0 = estimators.day0_scale(cell)
    y_ub = estimators.anticipation_scale(day0)
    calendar = panel.market_calendar(pl.scan_parquet(out / "prices" / "*.parquet").select("date").unique().collect()["date"])
    forecast = disclosures.continuation_forecast(
        pl.read_parquet(out / "client_days.parquet"), table, calendar, cut_before=estimators.G1_WINDOW[0]
    )
    s1 = estimators.information_bound(cell.join(forecast, on=["date", "symbol"], how="left"), y_ub)
    breakdown = next((k for k, (_, pk) in s1.items() if pk > 0.05), None)

    placebo = estimators.placebo_loadings(cell, GROUPS)
    clean = [g for g in GROUPS if all(placebo[leg][g][1] > 0.05 for leg in placebo)]
    stacked = estimators.stacked_legs(cell, GROUPS, legs=PATH_LEGS)
    s2 = {g: estimators.retention_tests(stacked, g) for g in clean}
    _, s3 = estimators.cross_news(pl.read_parquet(out / "cross_table.parquet"))
    s4_fit, s4_p = estimators.prop_sign(table)

    pvalues = {"S1_k1": s1[1][1], "S3": s3[1], "S4": s4_p}
    pvalues |= {f"S2_{g}_{name}": test[1] for g, tests in s2.items() for name, test in tests.items()}
    return {
        "counts": counts,
        "descriptive_only": min(counts.values()) < 1000,  # M2 power gate
        "primary": {
            **{f"theta_{g}": float(primary.coef[i]) for i, g in enumerate(GROUPS)},
            **{f"se_{g}": float(primary.se[i]) for i, g in enumerate(GROUPS)},
            **_test(z, p),
        },
        "day0": {"Y_obs": float(day0.coef[0]), "se": float(day0.se[0]), "Y_UB": y_ub},
        "s1": {str(k): _test(*v) for k, v in s1.items()},
        "s1_breakdown_k": breakdown,
        "placebo": {leg: {g: _test(*v) for g, v in by_group.items()} for leg, by_group in placebo.items()},
        "s2": {g: {name: _test(*v) for name, v in tests.items()} for g, tests in s2.items()},
        "path": {g: _path(stacked, g) for g in GROUPS},
        "s3": _test(*s3),
        "s4": {"theta_P": float(s4_fit.coef[0]), "se": float(s4_fit.se[0]), "p": s4_p},
        "holm": estimators.holm(pvalues),
        "binned": binned_day0(cell),
    }


def table1(results: dict) -> pl.DataFrame:
    """Table 1 rows: statistic, estimate, standard error or z, p."""
    rows = [
        (f"theta_{g} (overnight per sqrt(x), n={results['counts'][g]})", results["primary"][f"theta_{g}"], results["primary"][f"se_{g}"], None)
        for g in GROUPS
    ]
    rows.append(("Primary: theta_I - theta_R (z, p)", None, results["primary"]["z"], results["primary"]["p"]))
    rows.append(("D1 pooled day-0 Y_obs (se)", results["day0"]["Y_obs"], results["day0"]["se"], None))
    rows += [(f"S1 information bound, k={k} (z, one-sided p)", None, v["z"], v["p"]) for k, v in results["s1"].items()]
    rows += [(f"S2 {g} {name} (z, p)", None, v["z"], v["p"]) for g, tests in results["s2"].items() for name, v in tests.items()]
    rows.append(("S3 cross news alpha_I - alpha_R (z, p)", None, results["s3"]["z"], results["s3"]["p"]))
    rows.append(("S4 prop theta_P (se, one-sided p)", results["s4"]["theta_P"], results["s4"]["se"], results["s4"]["p"]))
    return pl.DataFrame(rows, schema=["statistic", "estimate", "se_or_z", "p"], orient="row")


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK_2)


def figure1(results: dict, path: Path) -> None:
    """(A) cumulative sigma-unit path per sqrt(x) by group with the disclosure marker; (B) the binned
    day-0 curve with the delta = 1/2 reference Y_obs * sqrt(x)."""
    fig, (left, right) = plt.subplots(1, 2, figsize=(12, 4.6), dpi=200, facecolor=SURFACE)
    positions = np.arange(len(PATH_LABELS))
    for g in GROUPS:
        values, se = np.array(results["path"][g]["values"]), np.array(results["path"][g]["se"])
        left.fill_between(positions, values - 1.96 * se, values + 1.96 * se, color=SERIES[g], alpha=0.10, linewidth=0)
        left.plot(positions, values, color=SERIES[g], linewidth=1.5, marker="o", markersize=5,
                  markeredgecolor=SURFACE, markeredgewidth=1.5, label=GROUP_NAMES[g])
    ends = {g: results["path"][g]["values"][-1] for g in GROUPS}
    low, high = left.get_ylim()
    if abs(ends["I"] - ends["R"]) > 0.06 * (high - low):  # colliding end labels are dropped; the legend stays
        for g, end in ends.items():
            left.annotate(GROUP_NAMES[g], (positions[-1], end), xytext=(6, 0), textcoords="offset points",
                          va="center", color=INK, fontsize=8)
    left.axvline(3.5, color=MUTED, linewidth=0.8)
    left.text(3.55, left.get_ylim()[1], "disclosure", color=INK_2, fontsize=8, va="top")
    left.axhline(0, color=AXIS, linewidth=0.8)
    left.set_xticks(positions, PATH_LABELS, fontsize=8)
    left.set_ylabel("cumulative abnormal return / sigma, per sqrt(Q/ADV)", color=INK_2, fontsize=8)
    left.set_title("A. Price path around a disclosed buy", color=INK, fontsize=10, loc="left")
    left.legend(frameon=False, fontsize=8, labelcolor=INK_2)

    binned = pl.DataFrame(results["binned"])
    for g, dodge in zip(GROUPS, (0.96, 1.04)):  # small multiplicative offset so the groups' bins do not overprint
        part = binned.filter(pl.col("group") == g)
        # percentile intervals can miss the point estimate on skewed bins; never draw a negative arm
        err = np.clip(np.vstack([part["mean"] - part["lo"], part["hi"] - part["mean"]]), 0.0, None)
        right.errorbar(part["x"] * dodge, part["mean"], yerr=err, fmt="o", color=SERIES[g], markersize=5,
                       markeredgecolor=SURFACE, markeredgewidth=1.5, elinewidth=1.0, label=GROUP_NAMES[g])
    grid = np.geomspace(binned["x"].min(), binned["x"].max(), 100)
    right.plot(grid, results["day0"]["Y_obs"] * np.sqrt(grid), color=MUTED, linewidth=1.0, label="Y_obs x sqrt(x) (delta = 1/2)")
    right.axhline(0, color=AXIS, linewidth=0.8)
    right.set_xscale("log")
    right.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    right.set_xlabel("disclosed net flow / ADV (Q/ADV)", color=INK_2, fontsize=8)
    right.set_ylabel("day-0 abnormal return / sigma (impact plus selection)", color=INK_2, fontsize=8)
    right.set_title("B. Day-0 conditional response, binned", color=INK, fontsize=10, loc="left")
    right.legend(frameon=False, fontsize=8, labelcolor=INK_2)
    for ax in (left, right):
        _style(ax)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def write_outputs(results: dict, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    table1(results).write_csv(dest / "table1.csv")
    figure1(results, dest / "figure1.png")
