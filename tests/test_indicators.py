import numpy as np

from indicators import curvature_signal, hurst_exponent, log_returns, volatility_adjusted_momentum, zscore


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


def test_zscore_zeroes_flat_series() -> None:
    values = np.array([5.0, 5.0, 5.0])
    assert np.array_equal(zscore(values), np.zeros_like(values))
