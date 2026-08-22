"""
SafeOpsAI Evaluation — Statistical Analyzer
============================================
Calculates descriptive statistics (mean, median, min, max, std dev, 95% CIs),
detects outliers via Interquartile Range (IQR), and executes paired statistical
hypothesis tests (Wilcoxon signed-rank / Paired t-test).
"""

import math
import numpy as np
from scipy import stats as scipy_stats
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

from .metrics import ExperimentRunRecord


class MetricSummary(BaseModel):
    metric_name: str
    scenario_id: str
    strategy: str
    sample_size: int
    mean: float
    median: float
    min: float
    max: float
    std_dev: float
    ci_95_lower: float
    ci_95_upper: float
    outlier_count: int = 0
    outliers_detected: List[float] = []
    robust_mean: float = 0.0
    robust_std_dev: float = 0.0


class StatisticalComparison(BaseModel):
    scenario_id: str
    metric_name: str
    baseline_strategy: str
    treatment_strategy: str
    baseline_mean: float
    treatment_mean: float
    absolute_difference: float
    relative_percentage_change: float
    test_used: str
    statistic_value: float
    p_value: float
    is_statistically_significant: bool
    description: str


def calculate_metric_summary(
    metric_name: str,
    scenario_id: str,
    strategy: str,
    observations: List[float],
) -> MetricSummary:
    """Computes full descriptive statistics, 95% CIs, and IQR outlier analysis."""
    clean_obs = [float(x) for x in observations if x is not None and not math.isnan(x)]
    n = len(clean_obs)

    if n == 0:
        return MetricSummary(
            metric_name=metric_name, scenario_id=scenario_id, strategy=strategy, sample_size=0,
            mean=0.0, median=0.0, min=0.0, max=0.0, std_dev=0.0, ci_95_lower=0.0, ci_95_upper=0.0
        )

    arr = np.array(clean_obs)
    mean_val = float(np.mean(arr))
    median_val = float(np.median(arr))
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))
    std_val = float(np.std(arr, ddof=1)) if n > 1 else 0.0

    # 95% Confidence Interval
    if n > 1 and std_val > 0:
        se = std_val / math.sqrt(n)
        h = se * scipy_stats.t.ppf(0.975, n - 1)
        ci_lower = float(mean_val - h)
        ci_upper = float(mean_val + h)
    else:
        ci_lower = mean_val
        ci_upper = mean_val

    # Outlier detection via IQR
    q25, q75 = np.percentile(arr, [25, 75])
    iqr = q75 - q25
    lower_bound = q25 - 1.5 * iqr
    upper_bound = q75 + 1.5 * iqr

    outliers = [float(x) for x in arr if x < lower_bound or x > upper_bound]
    non_outliers = [float(x) for x in arr if lower_bound <= x <= upper_bound]

    robust_mean = float(np.mean(non_outliers)) if non_outliers else mean_val
    robust_std = float(np.std(non_outliers, ddof=1)) if len(non_outliers) > 1 else std_val

    return MetricSummary(
        metric_name=metric_name,
        scenario_id=scenario_id,
        strategy=strategy,
        sample_size=n,
        mean=round(mean_val, 3),
        median=round(median_val, 3),
        min=round(min_val, 3),
        max=round(max_val, 3),
        std_dev=round(std_val, 3),
        ci_95_lower=round(ci_lower, 3),
        ci_95_upper=round(ci_upper, 3),
        outlier_count=len(outliers),
        outliers_detected=[round(x, 3) for x in outliers],
        robust_mean=round(robust_mean, 3),
        robust_std_dev=round(robust_std, 3),
    )


def compare_strategies_statistically(
    baseline_obs: List[float],
    treatment_obs: List[float],
    scenario_id: str,
    metric_name: str,
    baseline_strategy: str = "naive_restart",
    treatment_strategy: str = "safeopsai",
    alpha: float = 0.05,
) -> StatisticalComparison:
    """
    Executes paired statistical test between baseline and treatment strategy.
    Uses Wilcoxon signed-rank test if non-normal, or paired t-test.
    """
    b_arr = np.array(baseline_obs[:min(len(baseline_obs), len(treatment_obs))])
    t_arr = np.array(treatment_obs[:min(len(baseline_obs), len(treatment_obs))])

    b_mean = float(np.mean(b_arr)) if len(b_arr) > 0 else 0.0
    t_mean = float(np.mean(t_arr)) if len(t_arr) > 0 else 0.0
    abs_diff = t_mean - b_mean
    rel_pct = ((t_mean - b_mean) / b_mean * 100.0) if b_mean > 0 else 0.0

    if len(b_arr) < 3 or np.all(b_arr == t_arr):
        return StatisticalComparison(
            scenario_id=scenario_id,
            metric_name=metric_name,
            baseline_strategy=baseline_strategy,
            treatment_strategy=treatment_strategy,
            baseline_mean=round(b_mean, 3),
            treatment_mean=round(t_mean, 3),
            absolute_difference=round(abs_diff, 3),
            relative_percentage_change=round(rel_pct, 2),
            test_used="None (Insufficient variance or identical samples)",
            statistic_value=0.0,
            p_value=1.0,
            is_statistically_significant=False,
            description="Samples are identical or insufficient for paired hypothesis testing.",
        )

    # Check for normality of differences using Shapiro-Wilk test
    diffs = b_arr - t_arr
    stat_norm, p_norm = scipy_stats.shapiro(diffs) if len(diffs) >= 3 else (0, 0)

    if p_norm > 0.05:
        # Differences normally distributed -> Paired t-test
        test_name = "Paired Student's t-test"
        res = scipy_stats.ttest_rel(b_arr, t_arr)
        stat_val = float(res.statistic)
        p_val = float(res.pvalue)
    else:
        # Non-normal -> Wilcoxon signed-rank test
        test_name = "Wilcoxon Signed-Rank Test"
        try:
            res = scipy_stats.wilcoxon(b_arr, t_arr)
            stat_val = float(res.statistic)
            p_val = float(res.pvalue)
        except Exception:
            res = scipy_stats.ttest_rel(b_arr, t_arr)
            stat_val = float(res.statistic)
            p_val = float(res.pvalue)

    is_sig = p_val < alpha
    desc = (
        f"{treatment_strategy} demonstrated a statistically significant difference "
        f"(p={p_val:.4f} < {alpha}) compared to {baseline_strategy} on {metric_name} using {test_name}."
        if is_sig else
        f"No statistically significant difference observed (p={p_val:.4f} >= {alpha}) using {test_name}."
    )

    return StatisticalComparison(
        scenario_id=scenario_id,
        metric_name=metric_name,
        baseline_strategy=baseline_strategy,
        treatment_strategy=treatment_strategy,
        baseline_mean=round(b_mean, 3),
        treatment_mean=round(t_mean, 3),
        absolute_difference=round(abs_diff, 3),
        relative_percentage_change=round(rel_pct, 2),
        test_used=test_name,
        statistic_value=round(stat_val, 4),
        p_value=round(p_val, 4),
        is_statistically_significant=is_sig,
        description=desc,
    )
