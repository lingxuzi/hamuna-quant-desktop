"""交易日探针 (oracle) — 回答「今天是否 A 股交易日」。

消费方 (实时行情轮询 / 盘中分钟增量) 在周几+时段门控之后调用, 用于把
「工作日但休市」的节假日从轮询窗口里剔除; 返回 None (未知) 时调用方
维持现状行为 (周几近似 + 快照新鲜度判据兜底), 不引入新依赖。

探测链 (按确定性排序, 先到先得):
  1. fuyao 交易日历 (已配置 fuyao 时): GET /api/a-share/calendar/trading-days,
     今天在近一年交易日列表内 ⇔ 交易日。权威日历, 无时段依赖, 无开盘缓冲问题。
  2. easytdx 兜底 (未配 fuyao 时): 拉上证指数最近 1m K, 窗口 [今天 00:00, now]
     内有任何一根 ⇔ 今天开过盘。
  3. 都不可用 → None: 调用方按周几近似继续。

⚠ 为什么不复用行情源快照的时间戳判休市: 内置 easytdx 走 MAC 协议, 报价
只带本地时间戳 (无服务端时间), 非交易日快照戳的仍是「本地今天」—— 用它判
休市会把节假日误判成交易日。宁可返回「未知」, 也不给出错误结论 (错误结论
会直接停掉全市场行情轮询)。

⚠ 兜底为什么用分钟 K 而不是日 K: 分钟 K 实时推送, 盘中必有当天的; 部分
TDX 服务器的日 K 盘中不刷新, 拿它判休市会在交易日误停全市场轮询。

安全约束:
  - 周末直接返回 False (周几判断零成本, 不打任何请求)。
  - 探针是纯读: 只产出一个布尔判定, 不落盘、不进行情管道、不碰归属链路。
  - 只用于「降档」(休市不轮询); 休市结论 TTL 较短 (30 分钟) 定期复探,
    探针误判最坏损失一段快照且可自愈; 未知结论短 TTL (5 分钟) 防止
    轮询循环每拍重打失败的探测。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime

from app.market_time import cn_now
from app.services.shared_work import SharedWork, WorkBusyError

_TTL_TRADING_S = 3600.0   # 交易日结论每小时复探 (跨日天然失效)
_TTL_HOLIDAY_S = 1800.0   # 休市结论 30 分钟复探, 误判自愈上限
_TTL_UNKNOWN_S = 300.0    # 未知结论 5 分钟后重试探测

# 开盘缓冲: 过了这个时刻才敢把「今天没有 1m K」判成休市。
# 9:30 之后留 5 分钟给 TDX 首根 1m K 的生成延迟 —— 缓冲期内无 K 线只说明
# 「还没开盘」, 与「今天休市」在数据上无法区分, 只能返回未知。
_OPEN_GRACE_T = dtime(9, 35)
# 交易日兜底探针用的标的: 上证综指 (永不停牌, 不会因个股停牌误判休市)
_CALENDAR_SYMBOL = "000001.SH"

_CACHE_LOCK = threading.Lock()
_CACHE_EPOCH = 0
# Followers degrade to the documented "unknown" state quickly while a slow
# oracle finishes; they must not occupy the whole HTTP thread pool or probe again.
_PROBES = SharedWork("market.calendar", max_pending=8, wait_timeout_s=0.25)


@dataclass
class _Cache:
    day: object | None = None
    verdict: bool | None = None
    probed_at: float = 0.0


_CACHE = _Cache()


def reset_cache() -> None:
    """清空探针缓存 (测试用)。"""
    global _CACHE_EPOCH
    with _CACHE_LOCK:
        _CACHE_EPOCH += 1
        _CACHE.day = None
        _CACHE.verdict = None
        _CACHE.probed_at = 0.0


def _probe_fuyao(now: datetime) -> bool | None:
    """fuyao 交易日历: 今天在列表内 ⇔ 交易日。未配置 fuyao / 失败 → None。"""
    try:
        from app.data_providers import custom as custom_sources

        if not custom_sources.is_custom_provider("fuyao"):
            return None
        provider = custom_sources.get_provider("fuyao")
        days = provider.trading_days()
        return now.date() in days if days else None
    except Exception:  # noqa: BLE001 — 探针失败按未知处理, 不上抛
        return None


def _probe_easytdx(now: datetime) -> bool | None:
    """easytdx 兜底判据: 上证指数最近 1m K 里有没有今天的数据。

    MAC 协议没有交易日历接口, 只能用分钟 K 反推: 窗口 [今天 00:00, now]
    内有任何一根 ⇔ 今天开过盘。窗口取「今天零点起」而不是「最近 N 分钟」,
    这样午休 (11:30-13:00) 也有数据, 不会把午休误判成休市。

    拿不准时返回 None 而不是 False —— 错误结论会直接停掉全市场行情轮询,
    未知只是不做优化。
    """
    try:
        from app.data_providers import custom as custom_sources

        provider = custom_sources.get_provider("easytdx")
        if provider is None:
            return None
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        df = provider.get_minute(
            [_CALENDAR_SYMBOL], start_time=start, end_time=now, asset_type="index",
        )
        if df is None or df.height == 0:
            # 今天一根 1m 都没有: 开盘前 (未开盘) 与休市在数据上无法区分,
            # 过了开盘缓冲才敢判休市。
            return False if now.time() >= _OPEN_GRACE_T else None
        return True
    except Exception:  # noqa: BLE001 — 探针失败按未知处理, 不上抛
        return None


def is_trading_day(now: datetime | None = None) -> bool | None:
    """今天是否 A 股交易日。True=交易日, False=确定休市, None=未知 (维持周几近似)。

    周末零成本直判; 工作日走探测链 (fuyao 日历 → 未知),
    结论按 TTL 缓存。线程安全: 实时行情与分钟增量两个线程共用。
    """
    now = now or cn_now()
    if now.weekday() >= 5:
        return False

    with _CACHE_LOCK:
        epoch = _CACHE_EPOCH
        # 「未知」(None) 也是一个结论, 同样按 TTL 缓存 —— 它正是 _TTL_UNKNOWN_S 要
        # 挡住的场景 (未配 fuyao 时, 轮询每拍都会重打一次探测)。
        # _CACHE.day 只在探测写回时设置, 因此「当天已探过」用它判定即可。
        if (
            _CACHE.day == now.date()
            and (time.monotonic() - _CACHE.probed_at) < _ttl_of(_CACHE.verdict)
        ):
            return _CACHE.verdict

    def probe():
        # A caller can reach single-flight just after another probe retired.
        with _CACHE_LOCK:
            if (_CACHE.day == now.date()
                    and time.monotonic() - _CACHE.probed_at < _ttl_of(_CACHE.verdict)):
                return _CACHE.verdict
        verdict = _probe_fuyao(now)
        if verdict is None:
            # fuyao 日历未配 / 不可用 → easytdx 兜底 (未配 fuyao 时不该退化成周几近似,
            # 否则法定节假日会被当成交易日, 全市场白轮询一天)
            verdict = _probe_easytdx(now)

        with _CACHE_LOCK:
            if epoch != _CACHE_EPOCH:
                return None
            _CACHE.day = now.date()
            _CACHE.verdict = verdict
            _CACHE.probed_at = time.monotonic()
        return verdict

    try:
        return _PROBES.run((now.date(), epoch), probe)
    except WorkBusyError:
        return None


def _ttl_of(verdict: bool | None) -> float:
    if verdict is True:
        return _TTL_TRADING_S
    if verdict is False:
        return _TTL_HOLIDAY_S
    return _TTL_UNKNOWN_S
