"""实时行情轮询间隔下限契约测试。

下限语义 (中立能力原则): 轮询间隔下限是通用值 (1s), 不再按数据源档位
分档 —— 档位体系已下线, 各源自带节流策略。默认间隔仍是 6s (下限放宽 ≠ 改默认值)。
"""
from __future__ import annotations

from app.services.quote_service import QuoteService


def _bare() -> QuoteService:
    """绕过 __init__ (单例/线程副作用), 只用无状态方法。"""
    return QuoteService.__new__(QuoteService)


def test_min_interval_is_one_second():
    """实时轮询下限 = 1s (不分档)。"""
    assert _bare().get_min_interval() == 1.0


def test_clamp_keeps_one_second_and_bounds_upper():
    """设置 1s 不被抬到 6s; 低于下限抬到 1s, 高于上限压回上限。"""
    qs = _bare()
    assert qs._clamp_interval(1.0) == 1.0
    assert qs._clamp_interval(0.5) == 1.0
    assert qs._clamp_interval(120.0) == QuoteService.MAX_INTERVAL


def test_default_interval_unchanged():
    """默认间隔仍是 6s — 放宽的只是下限, 不是默认值。"""
    assert QuoteService.DEFAULT_INTERVAL == 6.0
