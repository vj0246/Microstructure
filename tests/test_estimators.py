from datetime import date

import numpy as np
import pandas as pd
import polars as pl
import pyfixest as pf

from impact.analysis import estimators


def test_censored_t_recovers_theta_where_ols_is_biased():
    rng = np.random.default_rng(7)
    n = 6000
    frame = pl.DataFrame(
        {
            "stock_type": rng.choice(["I", "R"], n),
            "x": np.exp(rng.normal(0.0, 1.0, n)),
            "bucket": rng.integers(0, 3, n),
        }
    )
    X, names = estimators.impact_design(frame, ("I", "R"))
    truth = np.array([0.5, 0.2, -0.1, 0.0, 0.1])
    latent = X @ truth + 0.8 * rng.standard_t(estimators.NU, n)
    cap = 1.0  # a band that censors about a fifth of the draws
    y = np.clip(latent, -cap, cap)
    upper, lower = latent >= cap, latent <= -cap
    stocks, dates = rng.integers(0, 300, n), rng.integers(0, 400, n)
    fit = estimators.censored_t(y, X, names, upper, lower, stocks, dates)
    assert fit.names[:2] == ("theta_I", "theta_R")
    assert np.all(np.abs(fit.coef[:5] - truth) < 3 * fit.se[:5])
    assert 0.7 < np.exp(fit.coef[-1]) < 0.9  # scale s = 0.8
    naive = estimators.ols(y, X, names, stocks, dates)
    assert naive.coef[0] < truth[0] - 3 * naive.se[0]  # ignoring the caps flattens the slope


def test_two_way_cluster_vcov_matches_pyfixest_on_ols():
    rng = np.random.default_rng(11)
    n = 800
    stock, date = rng.integers(0, 40, n), rng.integers(0, 50, n)
    x = rng.normal(size=n) + 0.5 * rng.normal(size=40)[stock]
    y = 1.0 + 2.0 * x + rng.normal(size=40)[stock] + rng.normal(size=50)[date] + rng.normal(size=n)
    X = np.column_stack([np.ones(n), x])
    ours = estimators.ols(y, X, ("Intercept", "x"), stock, date)
    theirs = pf.feols(
        "y ~ x",
        data=pd.DataFrame({"y": y, "x": x, "stock": stock, "date": date}),
        vcov={"CRV1": "stock+date"},
        ssc=pf.ssc(k_adj=False, G_adj=True, G_df="conventional"),
    )
    np.testing.assert_allclose(ours.coef, theirs.coef().to_numpy(), rtol=1e-10)
    np.testing.assert_allclose(ours.se, theirs.se().to_numpy(), rtol=1e-6)


def test_primary_cell_applies_every_preregistered_condition():
    base = {
        "date": date(2016, 5, 10), "side": 1, "series": "EQ", "stock_type": "I", "novel": False, "isolated": True,
        "news": False, "calendar_skip": False, "last_band": 0, "prev_last_raw": 50.0, "age": 300,
        "sigma": 0.02, "adv": 1e5, "bucket": 4, "g1": 0.001,
    }
    breaks = [
        {"date": date(2013, 3, 28)}, {"side": -1}, {"series": "BE"}, {"stock_type": "S"}, {"stock_type": "R"},
        {"isolated": False}, {"news": True}, {"calendar_skip": True}, {"last_band": 1}, {"prev_last_raw": 9.5},
        {"age": 40}, {"sigma": None}, {"g1": None},
    ]
    rows = [base, {**base, "stock_type": "R", "novel": True}] + [{**base, **b} for b in breaks]
    kept = estimators.primary_cell(pl.DataFrame(rows))
    assert kept.height == 2  # the untouched I row and the novel R row


def test_common_support_keeps_shared_buckets_and_overlapping_x():
    rng = np.random.default_rng(3)
    inst = pl.DataFrame({"stock_type": "I", "x": np.exp(rng.uniform(0, 3, 500)), "bucket": rng.integers(0, 3, 500)})
    ref = pl.DataFrame({"stock_type": "R", "x": np.exp(rng.uniform(1, 4, 500)), "bucket": rng.integers(0, 4, 500)})
    kept = estimators.common_support(pl.concat([inst, ref]))
    assert set(kept["bucket"].unique().to_list()) == {0, 1, 2}  # bucket 3 has no institutional events
    assert kept["x"].min() >= np.exp(1.0) and kept["x"].max() <= np.exp(3.0)


def test_stacked_legs_recover_each_leg_with_joint_covariance():
    rng = np.random.default_rng(5)
    n = 3000
    cell = pl.DataFrame(
        {
            "symbol": rng.integers(0, 400, n).astype(str),
            "date": rng.integers(0, 500, n),
            "stock_type": rng.choice(["I", "R"], n),
            "x": np.exp(rng.normal(0.0, 1.0, n)),
            "bucket": rng.integers(0, 3, n),
            "side": np.ones(n, dtype=int),
            "sigma": np.full(n, 0.02),
            "last_band": np.zeros(n, dtype=int),
            "open_band_next": np.zeros(n, dtype=int),
        }
    )
    truth = {"id": (0.8, 0.6), "g1": (0.3, 0.1), "d15": (-0.2, -0.1), "d620": (-0.1, 0.0)}
    root, is_i = np.sqrt(cell["x"].to_numpy()), cell["stock_type"].to_numpy() == "I"
    for leg, (t_i, t_r) in truth.items():
        if leg in ("id", "g1"):
            noise = rng.standard_t(4, n)
        else:
            noise = rng.normal(size=n)
            noise[rng.random(n) < 0.02] *= 15  # gross outliers the Huber legs must shrug off
        cell = cell.with_columns(pl.Series(f"{leg}_abn", 0.02 * (np.where(is_i, t_i, t_r) * root + noise)))
    fit = estimators.stacked_legs(cell, ("I", "R"))
    for leg, (t_i, _) in truth.items():
        i = fit.names.index(f"{leg}:theta_I")
        assert abs(fit.coef[i] - t_i) < 4 * fit.se[i], leg
    assert fit.vcov.shape == (len(fit.names), len(fit.names))
    tests = estimators.retention_tests(fit, "I")
    assert set(tests) == {"mechanical_5", "fair_5", "mechanical_20", "fair_20"}


def test_retention_contrasts_follow_the_llob_curve():
    names = ("id:theta_I", "g1:theta_I", "d15:theta_I", "d620:theta_I")
    fit = estimators.Fit(names, np.array([1.0, -0.5, -0.2, -0.2]), np.eye(4) * 0.01)
    k5 = np.sqrt(6) - np.sqrt(5)
    z, _ = estimators.retention_tests(fit, "I")["mechanical_5"]
    contrast = np.array([1 - k5, 1.0, 1.0, 0.0])
    assert np.isclose(z, (contrast @ fit.coef) / np.sqrt(contrast @ fit.vcov @ contrast))
    assert np.isclose(estimators.llob_retention(20), np.sqrt(21) - np.sqrt(20))


def synthetic_cell(n, seed, **legs):
    """Events with sigma 0.02 and leg_abn = 0.02 * (theta_type * sqrt(x) + t(4) noise) for each given leg."""
    rng = np.random.default_rng(seed)
    cell = pl.DataFrame(
        {
            "symbol": rng.integers(0, 400, n).astype(str),
            "date": rng.integers(0, 500, n),
            "stock_type": rng.choice(["I", "R"], n),
            "x": np.exp(rng.normal(0.0, 1.0, n)),
            "bucket": rng.integers(0, 3, n),
            "side": np.ones(n, dtype=int),
            "sigma": np.full(n, 0.02),
            "last_band": np.zeros(n, dtype=int),
            "open_band_next": np.zeros(n, dtype=int),
        }
    )
    root, is_i = np.sqrt(cell["x"].to_numpy()), cell["stock_type"].to_numpy() == "I"
    for leg, (t_i, t_r) in legs.items():
        signal = np.where(is_i, t_i, t_r) * root
        cell = cell.with_columns(pl.Series(f"{leg}_abn", 0.02 * (signal + rng.standard_t(4, n))))
    return cell


def test_day0_scale_and_anticipation_scale():
    fit = estimators.day0_scale(synthetic_cell(4000, 21, id=(0.7, 0.7)))
    assert abs(fit.coef[0] - 0.7) < 4 * fit.se[0]
    assert estimators.anticipation_scale(fit) == 1.0  # the bound never drops below Y = 1


def test_information_bound_shrinks_the_institutional_gap_as_k_grows():
    cell = synthetic_cell(4000, 22, g1=(0.3, 0.1)).with_columns(f_hat=pl.lit(0.5))
    tests = estimators.information_bound(cell, y_ub=1.0)
    assert tests[1][0] > tests[2][0] > tests[3][0]
    X, names = estimators.impact_design(cell, ("I", "R"))
    y = cell["g1_abn"].to_numpy() / 0.02
    none = np.zeros(cell.height, dtype=bool)
    plain = estimators.censored_t(y, X, names, none, none, cell["symbol"].to_numpy(), cell["date"].to_numpy())
    zero = estimators.information_bound(cell.with_columns(f_hat=pl.lit(0.0)), y_ub=1.0, ks=(1,))
    assert np.isclose(zero[1][0], plain.wald(np.r_[1.0, -1.0, np.zeros(len(names) - 1)])[0])


def test_placebo_loadings_flag_selection_on_the_prior_path():
    loadings = estimators.placebo_loadings(synthetic_cell(4000, 23, r_m1=(0.4, 0.0), r_m5=(0.0, 0.0)), ("I", "R"))
    assert loadings["r_m1"]["I"][1] < 0.001
    assert abs(loadings["r_m1"]["R"][0]) < 4 and abs(loadings["r_m5"]["I"][0]) < 4


def with_quality_columns(cell):
    """Columns quality() reads, set so that every row passes."""
    return cell.with_columns(
        date=pl.lit(date(2016, 5, 10)) + pl.duration(days=pl.col("date") % 1000),
        series=pl.lit("EQ"),
        news=pl.lit(False),
        calendar_skip=pl.lit(False),
        prev_last_raw=pl.lit(50.0),
        age=pl.lit(300),
        adv=pl.lit(1e5),
        g1=pl.lit(0.0),
    )


def test_prop_sign_detects_inventory_unwind():
    cell = synthetic_cell(3000, 31, g1=(-0.3, -0.3)).with_columns(stock_type=pl.lit("none"))
    fit, p = estimators.prop_sign(with_quality_columns(cell))
    assert fit.coef[0] < 0 and p < 0.001


def test_cross_news_contrasts_buyer_types_net_of_sellers():
    rng = np.random.default_rng(32)
    n = 4000
    cell = synthetic_cell(n, 33).with_columns(
        buyer_type=pl.Series(rng.choice(["I", "R"], n)), seller_type=pl.Series(rng.choice(["I", "R", "S"], n))
    )
    effect = 0.4 * (cell["buyer_type"] == "I").to_numpy() + 0.3 * (cell["seller_type"] == "S").to_numpy()
    cell = cell.with_columns(g1_abn=pl.Series(0.02 * (effect + rng.standard_t(4, n))))
    fit, (z, p) = estimators.cross_news(with_quality_columns(cell))
    assert fit.names[0] == "buyer_I_minus_R" and abs(fit.coef[0] - 0.4) < 4 * fit.se[0] and p < 0.001


def test_holm_steps_down():
    assert estimators.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == {"a": True, "c": False, "b": False}
    assert estimators.holm({"a": 0.01, "b": 0.02}) == {"a": True, "b": True}


def test_wald_contrast():
    fit = estimators.Fit(("a", "b"), np.array([1.0, 0.4]), np.array([[0.04, 0.01], [0.01, 0.04]]))
    z, p = fit.wald(np.array([1.0, -1.0]))
    assert np.isclose(z, 0.6 / np.sqrt(0.06))
    assert 0.0 < p < 0.05
