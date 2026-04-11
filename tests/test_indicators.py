import numpy as np

from indicators import (
    correlation_cluster_labels,
    curvature_signal,
    hurst_exponent,
    log_returns,
    volatility_adjusted_momentum,
    zscore,
)


def test_volatility_adjusted_momentum_returns_positive_value_on_trend() -> None:
    prices = np.linspace(10.0, 20.0, 80)
    returns = log_returns(prices)
    score = volatility_adjusted_momentum(
        prices=prices,
        returns=returns,
        lookback=20,
        skip=4,
        min_volatility=1e-8,
    )
    assert score > 0


def test_curvature_signal_is_positive_on_accelerating_series() -> None:
    returns = np.linspace(0.001, 0.01, 120) ** 1.2
    value = curvature_signal(returns, ma_window=8, signal_window=6)
    assert value > 0


def test_hurst_exponent_distinguishes_trend_from_noise() -> None:
    rng = np.random.default_rng(7)
    trending = np.cumsum(np.abs(rng.normal(0.02, 0.01, 256))) + 100
    noisy = np.cumsum(rng.normal(0.0, 0.02, 256)) + 100
    trending_hurst = hurst_exponent(trending)
    noisy_hurst = hurst_exponent(noisy)
    assert 0.55 < trending_hurst < 0.95
    assert 0.0 <= noisy_hurst <= 1.0
    assert trending_hurst > noisy_hurst


def test_hurst_exponent_does_not_saturate_on_smooth_trend() -> None:
    prices = 100 + np.cumsum(np.linspace(0.05, 0.2, 256))
    hurst = hurst_exponent(prices)
    assert 0.55 < hurst <= 1.0


def test_hurst_exponent_matches_reference_implementation_closely() -> None:
    rng = np.random.default_rng(11)
    prices = 100 + np.cumsum(rng.normal(0.03, 0.015, 256))
    fast_value = hurst_exponent(prices)
    reference_value = _reference_hurst_exponent(prices)
    assert np.isfinite(fast_value)
    assert np.isfinite(reference_value)
    assert abs(fast_value - reference_value) < 0.02


def test_zscore_zeroes_flat_series() -> None:
    values = np.array([5.0, 5.0, 5.0])
    assert np.array_equal(zscore(values), np.zeros_like(values))


def test_correlation_cluster_labels_groups_similar_series() -> None:
    base = np.cumsum(np.array([0.8, -0.2, 0.6, -0.1, 0.7, -0.15] * 14, dtype=float)) + 100.0
    history = {
        "AAAUSDT": base,
        "AABUSDT": (base * 0.995) + 0.5,
        "CCCUSDT": np.cumsum(np.array([-0.3, 0.5, -0.25, 0.45, -0.2, 0.4] * 14, dtype=float)) + 100.0,
    }
    labels = correlation_cluster_labels(
        history,
        lookback_bars=32,
        threshold=0.95,
    )
    assert labels["AAAUSDT"] == labels["AABUSDT"]
    assert labels["CCCUSDT"] != labels["AAAUSDT"]


def _reference_hurst_exponent(prices: np.ndarray, min_window: int = 8) -> float:
    prices = np.asarray(prices, dtype=float)
    if prices.size < 32 or np.any(prices <= 0):
        return float("nan")
    returns = log_returns(prices)
    if returns.size < 32:
        return float("nan")
    profile = np.cumsum(returns - returns.mean())
    max_window = profile.size // 4
    if max_window <= min_window:
        return float("nan")
    window_sizes = np.unique(np.geomspace(min_window, max_window, num=8).astype(int))
    fluctuation_points: list[tuple[int, float]] = []
    for window in window_sizes:
        chunk_count = profile.size // window
        if chunk_count < 2:
            continue
        t = np.arange(window, dtype=float)
        rms_values = []
        for idx in range(chunk_count):
            chunk = profile[idx * window : (idx + 1) * window]
            coeff = np.polyfit(t, chunk, 1)
            trend = np.polyval(coeff, t)
            residual = chunk - trend
            rms = float(np.sqrt(np.mean(residual**2)))
            if np.isfinite(rms) and rms > 0:
                rms_values.append(rms)
        if rms_values:
            fluctuation_points.append((window, float(np.mean(rms_values))))
    if len(fluctuation_points) < 2:
        return float("nan")
    window_logs = np.log(np.asarray([point[0] for point in fluctuation_points], dtype=float))
    fluctuation_logs = np.log(np.asarray([point[1] for point in fluctuation_points], dtype=float))
    slope, _ = np.polyfit(window_logs, fluctuation_logs, 1)
    return float(np.clip(slope, 0.0, 1.0))
