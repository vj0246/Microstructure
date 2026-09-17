import numpy as np

from impact.analysis import truncation_sim


def test_recovers_delta_without_truncation():
    clean = truncation_sim.Scenario(trigger=False, bands=False, churn=0.0, stocks=500, per_stock=20)
    frame = truncation_sim.draw(clean, seed=1)
    assert frame.height == 10_000
    assert abs(truncation_sim.profile_exponent(frame, np.arange(0.3, 0.71, 0.05)) - 0.5) <= 0.05
    assert abs(truncation_sim.binned_exponent(frame) - 0.5) < 0.15


def test_summary_reports_bias_per_scenario():
    runs = truncation_sim.run({"clean": truncation_sim.Scenario(trigger=False, bands=False, churn=0.0, stocks=200, per_stock=20)},
                        range(2), np.arange(0.3, 0.71, 0.1))
    row = truncation_sim.summary(runs).row(0, named=True)
    assert row["scenario"] == "clean" and row["events"] == 4000
    assert abs(row["profile_bias"]) <= 0.1


def test_binned_exponent_is_none_without_enough_positive_bins():
    import polars as pl

    flat = pl.DataFrame({"x": np.linspace(0.1, 1.0, 120), "y": -np.ones(120)})
    assert truncation_sim.binned_exponent(flat) is None


def test_trigger_keeps_only_large_gross_client_days():
    frame = truncation_sim.draw(truncation_sim.Scenario(stocks=300, per_stock=20), seed=2)
    assert 0 < frame.height < 6000
    assert frame["upper"].sum() + frame["lower"].sum() > 0  # bands censor some days
