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
