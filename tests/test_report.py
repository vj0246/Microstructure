import json
import subprocess

import pytest

from impact.analysis import report


def test_run_refuses_before_the_spec_tag(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(SystemExit, match="spec-v1"):
        report.require_spec(tmp_path)


def synthetic_results():
    test = {"z": 1.2, "p": 0.23}
    path = {"values": [-0.1, 0.0, 0.2, 0.9, 1.0, 0.8, 0.6], "se": [0.05] * 7}
    binned = [
        {"group": g, "x": x, "mean": m, "lo": m - 0.1, "hi": m + 0.1}
        for g in ("I", "R")
        for x, m in ((0.1, 0.2), (0.5, 0.5), (2.0, 1.0), (8.0, 1.9))
    ]
    return {
        "counts": {"I": 1200, "R": 1100},
        "descriptive_only": False,
        "primary": {"theta_I": 0.3, "theta_R": 0.1, "se_I": 0.05, "se_R": 0.05, **test},
        "day0": {"Y_obs": 0.7, "se": 0.03, "Y_UB": 1.0, "anchor": 0.1},
        "s1": {"1": test, "2": test, "3": test},
        "s1_breakdown_k": 1,
        "placebo": {"r_m1": {"I": test, "R": test}},
        "s2": {"I": {"mechanical_5": test}},
        "path": {"I": path, "R": path},
        "s3": test,
        "s4": {"theta_P": -0.2, "se": 0.05, "p": 0.001},
        "holm": {"S1_k1": False},
        "binned": binned,
        "robustness": {"1 official close baseline (g1c)": {"n": 10, "theta_I": 0.3, "theta_R": 0.1, **test}},
        "robustness_not_run": ["3 pre-2013 opens"],
    }


def test_outputs_are_written(tmp_path):
    report.write_outputs(synthetic_results(), tmp_path)
    assert json.loads((tmp_path / "results.json").read_text())["counts"]["I"] == 1200
    assert (tmp_path / "figure1.png").stat().st_size > 10_000
    assert "official close" in (tmp_path / "robustness.csv").read_text()
    table = report.table1(synthetic_results())
    assert table.height == 2 + 1 + 1 + 3 + 1 + 1 + 1


def synthetic_cell(side, n=600, seed=3):
    import numpy as np
    import polars as pl

    rng = np.random.default_rng(seed)
    kind = np.where(np.arange(n) % 2 == 0, "I", "R")
    x = rng.uniform(0.1, 4.0, n)
    theta = np.where(kind == "I", 0.5, 0.1)
    g1 = side * theta * np.sqrt(x) + 0.3 * rng.standard_normal(n)  # signed response, sigma = 1
    return pl.DataFrame({
        "symbol": [f"S{i % 60}" for i in range(n)], "date": [f"D{i % 80}" for i in range(n)],
        "side": [side] * n, "stock_type": kind, "x": x, "bucket": rng.integers(0, 3, n),
        "sigma": 1.0, "g1_abn": g1, "id_abn": 0.0, "open_band_next": [0] * n,
    })


def test_contrast_is_signed_so_sells_read_like_buys():
    for side in (1, -1):
        out = report._contrast(synthetic_cell(side))
        assert abs(out["theta_I"] - 0.5) < 0.1 and abs(out["theta_R"] - 0.1) < 0.1 and out["z"] > 3


def test_pooled_design_gets_bucket_by_side_effects():
    import polars as pl

    pooled = pl.concat([synthetic_cell(1, n=40), synthetic_cell(-1, n=40)])
    _, names = report.design(pooled)
    assert len([n for n in names if n.startswith("bucket_")]) == 6


def test_v2_outputs_are_written(tmp_path):
    base = synthetic_results()
    row = {"n": 10, "theta_I": 0.3, "theta_R": 0.1, "z": 1.2, "p": 0.23}
    results = {
        "c2": {"counts": {"I": 1100, "R": 1050}, "descriptive_only": False, "test": row, "path": base["path"],
               "placebo": base["placebo"], "robustness": {"2 uncensored OLS": row}},
        "e1_exploratory": {"counts": {"I": 1900, "R": 1800}, "test": row},
    }
    report.write_outputs_v2(results, tmp_path)
    assert report.table2(results).height == 3
    assert (tmp_path / "figure2.png").stat().st_size > 10_000
    assert json.loads((tmp_path / "results_v2.json").read_text())["c2"]["counts"]["R"] == 1050


def quality_cell(side=1, n=800, seed=5, premium_where=None):
    """A synthetic cell that passes estimators.quality(): real dates, delivery data, prices and an optional
    institutional premium confined to the rows picked by `premium_where` (a boolean array)."""
    from datetime import date, timedelta

    import numpy as np
    import polars as pl

    cell = synthetic_cell(side, n=n, seed=seed)
    rng = np.random.default_rng(seed)
    high = rng.random(n) < 0.5
    institutional = cell["stock_type"].to_numpy() == "I"
    where = high if premium_where is None else premium_where
    g1 = side * 0.4 * np.sqrt(cell["x"].to_numpy()) * (institutional & where) + 0.3 * rng.standard_normal(n)
    return cell.with_columns(
        date=pl.Series([date(2016, 1, 1) + timedelta(days=i % 80) for i in range(n)]),
        g1_abn=pl.Series(g1), g1=pl.Series(g1), volume=pl.lit(1000), deliv_qty=pl.Series(np.where(high, 700, 300)),
        prev_last_raw=pl.Series(rng.uniform(20, 300, n)), news=pl.lit(False), calendar_skip=pl.lit(False),
        last_band=pl.lit(0), age=pl.lit(500), series=pl.lit("EQ"), adv=pl.lit(1.0),
    )


def test_delivery_flags_split_at_the_bucket_year_median_of_every_event():
    cell = quality_cell()
    flagged = report.delivery_flags(cell, cell)
    assert flagged.height == cell.height
    assert 0.3 < flagged["high"].mean() < 0.7  # the 700/1000 vs 300/1000 shares straddle each bucket median


def test_delivery_interaction_finds_a_premium_that_lives_in_high_delivery_events():
    cell = quality_cell()
    out = report.delivery_test(cell, cell)
    assert out["interaction: high minus low"]["estimate"] > 0.2
    assert out["interaction: high minus low"]["p_one_sided"] < 0.05
    assert sum(out["counts"].values()) == cell.height


def test_tick_test_restricts_by_price_and_adds_a_log_price_variant():
    out = report.tick_test(quality_cell())
    assert out["price >= 50 (primary)"]["n"] < out["full cell"]["n"]
    assert out["price >= 100"]["n"] < out["price >= 50 (primary)"]["n"]
    assert out["full cell, ln price control"]["n"] == out["full cell"]["n"]


def test_retention_levels_report_peak_t5_t20_and_placebo_status():
    import numpy as np
    import polars as pl

    cell = quality_cell(n=800)
    rng = np.random.default_rng(9)
    legs = {leg: 0.2 * rng.standard_normal(cell.height) for leg in ("ov_abn", "id_abn", "d15_abn", "d620_abn", "r_m1_abn", "r_m5_abn")}
    cell = cell.with_columns(**{k: pl.Series(v) for k, v in legs.items()})
    out = report.retention_levels(cell)
    assert set(out) == {"I", "R"}
    assert set(out["I"]["levels"]) == {"peak (t+1 open)", "t+5", "t+20"}
    assert isinstance(out["I"]["placebo_clean"], bool)
