"""除权参考价: float32 噪声不该决定 x.xx5 分界上的舍入方向。

上游 (TDX 协议) 字段全是 float32 —— 收盘价 11.41 存成 11.40999984741211,
派息 0.035 (每10股 0.35) 存成 0.034999999403953555。两者相减正好压在
11.375 这个半分界上, float 路径 (floor(x*100+0.5)) 被噪声带偏 → 参考价 11.37
(交易所是 11.38), 单事件因子差 0.09% → 除权日限价差 1 分, 收盘贴着限价时
会被误判成涨跌停 (实测 556/21665 行 / 470 只标的)。

供应商自身按股本比例推出的值 (送转 0.3999975919723511, 相对偏差 ~6e-6)
不是 float32 噪声, 不许还原到网格 —— 还原会把误差放大到 0.2 分。
"""

import pytest

from app.data_providers.ex_factor import ref_price, recompute_ex_factor


def test_float32_noise_does_not_flip_half_cent_rounding():
    """000009.SZ 2023-08-03: 11.41 - 0.035 = 11.375 → 交易所 11.38, 不是 11.37。"""
    ref = ref_price(11.40999984741211, 0.034999999403953555, 0.0, 0.0, 0.0)

    assert ref == 11.38


def test_vendor_imprecise_value_is_not_snapped_to_grid():
    """000002.SZ 2022-08-25: 派息 0.9761257171630859 是供应商按股本比例推出的值,
    不还原到 3 位网格 (还原会把 0.98 的真值改成 0.976, 参考价差 1 分)。"""
    ref = ref_price(16.6200008392334, 0.9761257171630859, 0.0, 0.0, 0.0)

    assert ref == 15.64


def test_clean_inputs_keep_exchange_formula():
    assert ref_price(10.0, 1.0, 0.0, 0.0, 0.0) == 9.0
    assert ref_price(10.0, 0.0, 0.0, 0.3, 5.0) == 8.85  # (10 + 0.3×5) / 1.3
    assert ref_price(20.0, 0.0, 0.2, 0.0, 0.0) == 16.67  # 10送2: 20 / 1.2


def test_invalid_denominator_returns_none():
    assert ref_price(10.0, 0.0, 0.0, -1.0, 0.0) is None


def test_recompute_ex_factor_reproduces_stored_factor():
    """存量归一化: 明细列 → 因子, 与 factor_rows 同口径。"""
    factor = recompute_ex_factor(11.40999984741211, 0.034999999403953555, 0.0, 0.0, 0.0)

    assert factor == pytest.approx(11.40999984741211 / 11.38)
    assert factor != pytest.approx(11.40999984741211 / 11.37)  # 旧 float 口径


def test_recompute_ex_factor_returns_none_without_details():
    assert recompute_ex_factor(None, 0.1, 0.0, 0.0, 0.0) is None
    assert recompute_ex_factor(0.0, 0.1, 0.0, 0.0, 0.0) is None
