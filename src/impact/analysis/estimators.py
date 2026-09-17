"""Estimators (PLAN section 6): a Student-t likelihood with known censoring points, OLS, and
multi-way cluster-robust sandwich covariances built from per-event scores."""

import math
from dataclasses import dataclass
from datetime import date
from itertools import combinations

import numpy as np
import polars as pl
from scipy import optimize, stats
from scipy.linalg import block_diag

NU = 4.0  # fixed degrees of freedom (PLAN J6)
G1_WINDOW = (date(2013, 4, 1), date(2026, 7, 31))  # call-auction opens for all EQ stocks; before the closing auction
PRICE_FLOOR = 10.0
MIN_AGE = 70  # trading days since listing


def quality(table: pl.DataFrame) -> pl.DataFrame:
    """Conditions every confirmatory cell shares: g1 window, EQ series, no news or calendar skip, LAST_t
    off the bands, unadjusted price >= Rs 10, listed >= 70 days, and sigma, bucket and g1 known with ADV positive (a zero-volume median ADV
    leaves x undefined)."""
    return table.filter(
        pl.col("date").is_between(*G1_WINDOW)
        & (pl.col("series") == "EQ")
        & ~pl.col("news")
        & ~pl.col("calendar_skip")
        & (pl.col("last_band") == 0)
        & (pl.col("prev_last_raw") >= PRICE_FLOOR)
        & (pl.col("age") >= MIN_AGE)
        & pl.all_horizontal(pl.col("sigma", "adv", "bucket", "g1").is_not_null())
        & (pl.col("adv") > 0)
    )


def primary_cell(table: pl.DataFrame, side: int = 1) -> pl.DataFrame:
    """PLAN section 7 cell before common support: quality() plus stock-days of type I or R at the 80%
    threshold (R also novel) that are backward-isolated, on one side: buys (v1) or sells (SPEC v2, C2)."""
    return quality(table).filter(
        (pl.col("side") == side)
        & pl.col("stock_type").is_in(["I", "R"])
        & ((pl.col("stock_type") == "I") | pl.col("novel"))
        & pl.col("isolated")
    )


def common_support(cell: pl.DataFrame, groups: tuple[str, ...] = ("I", "R")) -> pl.DataFrame:
    """PLAN section 7: keep buckets present in every group, and x inside the overlap of the groups'
    1st-99th percentile ranges."""
    members = [cell.filter(pl.col("stock_type") == g) for g in groups]
    shared = set.intersection(*(set(m["bucket"].unique().to_list()) for m in members))
    low = max(m["x"].quantile(0.01) for m in members)
    high = min(m["x"].quantile(0.99) for m in members)
    return cell.filter(pl.col("bucket").is_in(sorted(shared)) & pl.col("x").is_between(low, high))


@dataclass(frozen=True)
class Fit:
    names: tuple[str, ...]
    coef: np.ndarray
    vcov: np.ndarray
    loglik: float = float("nan")  # set by likelihood estimators

    @property
    def se(self) -> np.ndarray:
        return np.sqrt(np.diag(self.vcov))

    def wald(self, contrast: np.ndarray, value: float = 0.0) -> tuple[float, float]:
        """z statistic and two-sided p-value of contrast @ coef = value."""
        z = (contrast @ self.coef - value) / np.sqrt(contrast @ self.vcov @ contrast)
        return float(z), float(2 * stats.norm.sf(abs(z)))


def cluster_vcov(scores: np.ndarray, bread: np.ndarray, *clusters: np.ndarray) -> np.ndarray:
    """bread^-1 meat bread^-1 with multi-way clustering (Cameron, Gelbach and Miller 2011): the meat sums
    the clustered outer products over every non-empty subset of cluster dimensions (intersections of
    dimensions), with sign (-1)^(size+1) and a G/(G-1) factor each. Negative eigenvalues are clipped."""
    inverse = np.linalg.inv(bread)
    meat = np.zeros_like(bread)
    codes = [np.unique(c, return_inverse=True)[1].ravel() for c in clusters]  # labels of any dtype -> ints
    for size in range(1, len(codes) + 1):
        for subset in combinations(codes, size):
            _, ids = np.unique(np.column_stack(subset), axis=0, return_inverse=True)
            groups = ids.max() + 1
            summed = np.zeros((groups, scores.shape[1]))
            np.add.at(summed, ids.ravel(), scores)
            meat += (-1) ** (size + 1) * groups / (groups - 1) * summed.T @ summed
    vcov = inverse @ meat @ inverse.T
    values, vectors = np.linalg.eigh((vcov + vcov.T) / 2)
    return vectors @ np.diag(np.clip(values, 0.0, None)) @ vectors.T


def ols(y: np.ndarray, X: np.ndarray, names: tuple[str, ...], *clusters: np.ndarray) -> Fit:
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    scores = X * (y - X @ coef)[:, None]
    return Fit(names, coef, cluster_vcov(scores, X.T @ X, *clusters))


def _t_pieces(params: np.ndarray, y: np.ndarray, X: np.ndarray, upper: np.ndarray, lower: np.ndarray, nu: float):
    """Per-row log-likelihood and scores of y = X b + s e, e ~ t(nu); censored rows only reveal that
    the latent value is >= y (upper) or <= y (lower). Parameters are (b, log s)."""
    b, log_s = params[:-1], params[-1]
    s = np.exp(log_s)
    z = (y - X @ b) / s
    pdf, cdf, sf = stats.t.pdf(z, nu), stats.t.cdf(z, nu), stats.t.sf(z, nu)
    with np.errstate(divide="ignore", invalid="ignore"):
        loglik = np.where(upper, np.log(sf), np.where(lower, np.log(cdf), stats.t.logpdf(z, nu) - log_s))
        d_mu = np.where(upper, pdf / (s * sf), np.where(lower, -pdf / (s * cdf), (nu + 1) * z / (s * (nu + z**2))))
        d_log_s = np.where(upper, pdf * z / sf, np.where(lower, -pdf * z / cdf, (nu + 1) * z**2 / (nu + z**2) - 1))
    return loglik, np.column_stack([X * d_mu[:, None], d_log_s])


def censored_t(
    y: np.ndarray,
    X: np.ndarray,
    names: tuple[str, ...],
    upper: np.ndarray,
    lower: np.ndarray,
    *clusters: np.ndarray,
    nu: float = NU,
) -> Fit:
    """Maximum likelihood with a cluster-robust sandwich; the last coefficient is log s."""
    params, scores, bread = _censored_t_parts(y, X, upper, lower, nu)
    loglik = float(_t_pieces(params, y, X, upper, lower, nu)[0].sum())
    return Fit((*names, "log_s"), params, cluster_vcov(scores, bread, *clusters), loglik)


def _censored_t_parts(
    y: np.ndarray, X: np.ndarray, upper: np.ndarray, lower: np.ndarray, nu: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Estimates (b, log s), per-row scores, and bread = minus the Hessian of the log-likelihood."""
    start_b, *_ = np.linalg.lstsq(X, y, rcond=None)
    start = np.append(start_b, np.log(np.std(y - X @ start_b)))

    def negative(params: np.ndarray) -> tuple[float, np.ndarray]:
        loglik, scores = _t_pieces(params, y, X, upper, lower, nu)
        return -loglik.sum(), -scores.sum(axis=0)

    result = optimize.minimize(negative, start, jac=True, method="BFGS", options={"gtol": 1e-8, "maxiter": 2000})
    # BFGS reports "precision loss" when the line search stalls at the optimum; accept it if the
    # average score is numerically zero.
    if not result.success and np.max(np.abs(result.jac)) / len(y) > 1e-6:
        raise RuntimeError(f"censored t fit did not converge: {result.message}")
    params = result.x
    _, scores = _t_pieces(params, y, X, upper, lower, nu)
    step = 1e-5 * np.maximum(1.0, np.abs(params))
    columns = []
    for j, h in enumerate(step):  # bread = minus the Hessian, from central differences of the analytic gradient
        up, down = params.copy(), params.copy()
        up[j] += h
        down[j] -= h
        columns.append((negative(up)[1] - negative(down)[1]) / (2 * h))
    bread = np.column_stack(columns)
    return params, scores, (bread + bread.T) / 2


HUBER_C = 1.345  # 95% efficiency under normal errors


def _huber_parts(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Huber M-estimate of y = X b + e by iteratively reweighted least squares, with the scale fixed at
    the normalized MAD of the OLS residuals. Returns b, per-row scores psi(u) x and the bread."""
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    scale = 1.4826 * np.median(np.abs(resid - np.median(resid)))
    for _ in range(200):
        u = (y - X @ coef) / scale
        root_w = np.sqrt(np.where(np.abs(u) <= HUBER_C, 1.0, HUBER_C / np.abs(u)))
        updated, *_ = np.linalg.lstsq(X * root_w[:, None], y * root_w, rcond=None)
        if np.max(np.abs(updated - coef)) < 1e-10:
            coef = updated
            break
        coef = updated
    u = (y - X @ coef) / scale
    inside = (np.abs(u) <= HUBER_C).astype(float)
    return coef, X * np.clip(u, -HUBER_C, HUBER_C)[:, None], X.T @ (X * inside[:, None]) / scale


LEG_CENSOR = {"id": "last_band", "g1": "open_band_next"}  # band flag that censors each leg


def stacked_legs(cell: pl.DataFrame, groups: tuple[str, ...], legs: tuple[str, ...] = ("id", "g1", "d15", "d620")) -> Fit:
    """Joint fit of the signed, sigma-scaled abnormal legs on one design (PLAN S2): censored t for id and
    g1 (band flags, flipped with the side), Huber for d15 and d620. Per-event scores stack, so the
    covariance across legs is two-way clustered by stock and date. Names read "<leg>:<coef>"."""
    X, names = impact_design(cell, groups)
    side, sigma = cell["side"].to_numpy(), cell["sigma"].to_numpy()
    params, scores, breads, labels = [], [], [], []
    for leg in legs:
        y = side * cell[f"{leg}_abn"].to_numpy() / sigma
        if leg in LEG_CENSOR:
            band = side * cell[LEG_CENSOR[leg]].to_numpy()
            p, s, b = _censored_t_parts(y, X, band > 0, band < 0, NU)
            labels += [f"{leg}:{n}" for n in (*names, "log_s")]
        else:
            p, s, b = _huber_parts(y, X)
            labels += [f"{leg}:{n}" for n in names]
        params.append(p)
        scores.append(s)
        breads.append(b)
    vcov = cluster_vcov(np.hstack(scores), block_diag(*breads), cell["symbol"].to_numpy(), cell["date"].to_numpy())
    return Fit(tuple(labels), np.concatenate(params), vcov)


def llob_retention(tau: int) -> float:
    """Share of peak impact left tau days after a one-day metaorder: sqrt(1 + tau) - sqrt(tau) (T = 1)."""
    return math.sqrt(1 + tau) - math.sqrt(tau)


def retention_tests(fit: Fit, group: str) -> dict[str, tuple[float, float]]:
    """PLAN J7 Wald tests on a stacked_legs fit: R(tau) - k * b_id = 0 for tau in 5 and 20, with
    R(5) = b_id + b_g1 + b_d15 and R(20) = R(5) + b_d620; k = K(tau) under pure mechanics (phi_inf = 0)
    and k = 2/3 + K(tau)/3 under fair pricing (phi_inf = 2/3). Values are (z, two-sided p)."""
    index = {name: i for i, name in enumerate(fit.names)}

    def unit(leg: str) -> np.ndarray:
        vector = np.zeros(len(fit.names))
        vector[index[f"{leg}:theta_{group}"]] = 1.0
        return vector

    cumulative = {5: unit("id") + unit("g1") + unit("d15")}
    cumulative[20] = cumulative[5] + unit("d620")
    tests = {}
    for tau, path in cumulative.items():
        k = llob_retention(tau)
        tests[f"mechanical_{tau}"] = fit.wald(path - k * unit("id"))
        tests[f"fair_{tau}"] = fit.wald(path - (2 / 3 + k / 3) * unit("id"))
    return tests


def impact_design(frame: pl.DataFrame, groups: tuple[str, ...] | None) -> tuple[np.ndarray, tuple[str, ...]]:
    """PLAN section 6 mean: theta_k * 1{stock_type = k} * sqrt(x) for each group k (one pooled "theta"
    when groups is None), plus one dummy per benchmark bucket (lambda_b, no separate intercept)."""
    root = np.sqrt(frame["x"].to_numpy())
    if groups is None:
        columns, labels = [root], ["theta"]
    else:
        kinds = frame["stock_type"].to_numpy()
        columns, labels = [np.where(kinds == g, root, 0.0) for g in groups], [f"theta_{g}" for g in groups]
    buckets = np.sort(frame["bucket"].unique().to_numpy())
    columns += [(frame["bucket"].to_numpy() == b).astype(float) for b in buckets]
    return np.column_stack(columns), (*labels, *(f"bucket_{b}" for b in buckets))


def _signed(cell: pl.DataFrame, leg: str) -> np.ndarray:
    """side * abnormal leg / sigma."""
    return cell["side"].to_numpy() * cell[f"{leg}_abn"].to_numpy() / cell["sigma"].to_numpy()


def _clusters(cell: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    return cell["symbol"].to_numpy(), cell["date"].to_numpy()


def day0_scale(cell: pl.DataFrame) -> Fit:
    """PLAN D1: pooled conditional day-0 response of side * id_abn / sigma to sqrt(x) with bucket effects,
    censored where LAST_t sits at a band. `theta` is Y_obs: impact plus selection, not a causal scale."""
    X, names = impact_design(cell, None)
    band = cell["side"].to_numpy() * cell["last_band"].to_numpy()
    return censored_t(_signed(cell, "id"), X, names, band > 0, band < 0, *_clusters(cell))


def anticipation_scale(day0: Fit) -> float:
    """Y_UB = max(1, upper one-sided 95% bound of the pooled day-0 Y_obs) (PLAN S1)."""
    i = day0.names.index("theta")
    return max(1.0, float(day0.coef[i] + 1.645 * day0.se[i]))


def information_bound(cell: pl.DataFrame, y_ub: float, ks: tuple[int, ...] = (1, 2, 3)) -> dict[int, tuple[float, float]]:
    """PLAN S1: subtract UB = Y_UB (sqrt(x + k F-hat) - sqrt(x)) from institutional overnight outcomes and
    test theta*_I - theta_R <= 0 against > 0. Returns (z, one-sided p) per k; the breakdown k is the first
    whose p exceeds the test level. Events without f_hat are dropped."""
    cell = cell.drop_nulls("f_hat")
    X, names = impact_design(cell, ("I", "R"))
    y = _signed(cell, "g1")
    band = cell["side"].to_numpy() * cell["open_band_next"].to_numpy()
    x, f = cell["x"].to_numpy(), cell["f_hat"].to_numpy()
    institutional = cell["stock_type"].to_numpy() == "I"
    contrast = np.zeros(len(names) + 1)  # + log_s
    contrast[:2] = (1.0, -1.0)
    tests = {}
    for k in ks:
        bound = np.where(institutional, y_ub * (np.sqrt(x + k * f) - np.sqrt(x)), 0.0)
        z, _ = censored_t(y - bound, X, names, band > 0, band < 0, *_clusters(cell)).wald(contrast)
        tests[k] = (z, float(stats.norm.sf(z)))
    return tests


def cross_news(crosses: pl.DataFrame) -> tuple[Fit, tuple[float, float]]:
    """PLAN S3: overnight response on exact negotiated crosses (net flow zero), from the buyer's side:
    g1_abn / sigma on 1{buyer I} with buyer I or R, seller-type effects (first type as reference) and
    bucket effects, censored at the next open's band. The buyer-I coefficient is alpha_I - alpha_R;
    returns the fit and its (z, two-sided p)."""
    cell = quality(crosses).filter(pl.col("buyer_type").is_in(["I", "R"]))
    seller = cell["seller_type"].to_numpy()
    references = sorted(set(seller))[1:]
    buckets = np.sort(cell["bucket"].unique().to_numpy())
    columns = [cell["buyer_type"].to_numpy() == "I"] + [seller == s for s in references]
    columns += [cell["bucket"].to_numpy() == b for b in buckets]
    names = ("buyer_I_minus_R", *(f"seller_{s}" for s in references), *(f"bucket_{b}" for b in buckets))
    band = cell["open_band_next"].to_numpy()
    y = cell["g1_abn"].to_numpy() / cell["sigma"].to_numpy()
    fit = censored_t(y, np.column_stack(columns).astype(float), names, band > 0, band < 0, *_clusters(cell))
    return fit, fit.wald(np.eye(len(fit.names))[0])


def prop_sign(table: pl.DataFrame) -> tuple[Fit, float]:
    """PLAN S4: overnight response to churner-only net flow (stock_type "none", side != 0). Disclosure
    forecasts the unwind of contrarian inventory, so H: theta_P < 0; returns the fit and one-sided p."""
    cell = quality(table).filter((pl.col("stock_type") == "none") & (pl.col("side") != 0))
    X, names = impact_design(cell.with_columns(stock_type=pl.lit("P")), ("P",))
    band = cell["side"].to_numpy() * cell["open_band_next"].to_numpy()
    fit = censored_t(_signed(cell, "g1"), X, names, band > 0, band < 0, *_clusters(cell))
    z, _ = fit.wald(np.eye(len(fit.names))[0])
    return fit, float(stats.norm.cdf(z))


def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down rejections: in ascending p order, reject while p <= alpha / (tests remaining)."""
    ordered = sorted(pvalues, key=pvalues.__getitem__)
    rejected, going = {}, True
    for i, name in enumerate(ordered):
        going = going and pvalues[name] <= alpha / (len(ordered) - i)
        rejected[name] = going
    return rejected


PLACEBO_LEGS = ("r_m1", "r_m5")


def placebo_loadings(cell: pl.DataFrame, groups: tuple[str, ...]) -> dict[str, dict[str, tuple[float, float]]]:
    """PLAN D1 selection diagnostics: side * pre-event abnormal return / sigma on the event design (Huber,
    two-way clustered). A loading on sqrt(x) means flow selects on the prior path; S2 runs only for groups
    whose loadings are insignificant. Returns {leg: {group: (z, two-sided p)}}."""
    X, names = impact_design(cell, groups)
    loadings = {}
    for leg in PLACEBO_LEGS:
        coef, scores, bread = _huber_parts(_signed(cell, leg), X)
        fit = Fit(names, coef, cluster_vcov(scores, bread, *_clusters(cell)))
        loadings[leg] = {g: fit.wald(np.eye(len(names))[names.index(f"theta_{g}")]) for g in groups}
    return loadings
