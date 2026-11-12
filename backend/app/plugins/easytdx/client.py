"""easy_tdx 双通道封装 —— MacClient(行情) + TdxClient(除权事件)。

为什么分两个通道: easy_tdx 有两条并存的协议族。本机实测(2026-11)标准协议
TdxClient 的 K 线/行情命令在可用节点上恒返回 2 字节残缺响应(TdxDecodeError
"声明 800 条记录但响应体仅 2 字节"), 而 MAC 协议日K/分钟K/全市场快照/指数
全部正常 —— 库自带 CLI `easy-tdx kline` 走的也是 MAC 通道。除权事件
get_xdxr_info 是唯一两族都正常的命令, 沿用标准协议。

provider 不直接碰 easy_tdx: 全部调用经本模块, 便于契约测试注入假 client,
并在此处集中处理连接复用、线程串行化与符号映射。

连接池: 单个 EasyTdxClient = 一条 MAC 连接 + 一把 RLock(请求串行)。
并发拉取靠 **多实例**: 每个实例一条连接, 互不加锁(见 fork())。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 沪深北 market 代码(与 easy_tdx.mac.enums.Category 的 SH/SZ/BJ 一致, 但用裸 int
# 以免 client 顶层硬依赖 easy_tdx —— 模块未装时契约测试仍可 import 本文件)。
_MARKET_BY_SUFFIX = {"SH": 1, "SZ": 0, "BJ": 2}

# 日K/分钟K每页 700 根由 MacClient 内部自动翻页(count 传总数即可)。
_DAILY_MAX_COUNT = 6000  # ≈24 年; 服务端无此深度时自动在空页终止
_MINUTE_MAX_COUNT = 9600  # 服务端 1m 仅保留约 21 个交易日, 更深由服务端截断


class EasyTdxError(RuntimeError):
    """easy_tdx 调用失败的统一异常(连接、协议、解析)。provider 据此软失败。"""


def parse_symbol(symbol: str) -> tuple[int, str]:
    """'600519.SH' → (1, '600519')。后缀缺失或非法抛 EasyTdxError。"""
    code, _, suffix = (symbol or "").strip().upper().partition(".")
    market = _MARKET_BY_SUFFIX.get(suffix)
    if not code or market is None:
        raise EasyTdxError(f"无法解析标的代码 {symbol!r}: 需要 6 位代码 + .SH/.SZ/.BJ 后缀")
    return market, code


class EasyTdxClient:
    """懒连接的双通道封装。所有公开方法线程安全(内部 RLock 串行化)。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mac: Any = None
        self._std: Any = None

    # ---- 连接 ----
    def _require(self, module: str):
        try:
            return __import__(module, fromlist=["_"])
        except ImportError as e:  # 插件 unavailable 时 loader 不会走到这里
            raise EasyTdxError(f"easy_tdx 不可用: {e}") from e

    def _mac_client(self):
        if self._mac is not None:
            return self._mac
        mac = self._require("easy_tdx.mac.client")
        # require 触发真实接口探测, 避免选到只通 TCP 不供 K 线的节点;
        # strict=False: 无满足节点也退回延迟最低者(软失败比整源不可用强)。
        client = mac.MacClient.from_best_host(require=["kline", "quotes"], strict=False)
        client.connect()
        self._mac = client
        return client

    def _std_client(self):
        if self._std is not None:
            return self._std
        top = self._require("easy_tdx")
        client = top.TdxClient.from_best_host()
        client.connect()
        self._std = client
        return client

    def close(self) -> None:
        with self._lock:
            for attr in ("_mac", "_std"):
                client = getattr(self, attr)
                if client is not None:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001 — 关闭失败不影响重建
                        logger.debug("easy_tdx %s 关闭异常", attr, exc_info=True)
                    setattr(self, attr, None)

    def prepare(self) -> None:
        """预热 MAC 通道: 选优主机 + 建连。

        from_best_host 会 ping + 能力探测全部节点(约 1s), 并发分叉前先调一次,
        各路 fork 就只做建连, 不重复选优。
        """
        self._mac_client()

    def prepare_std(self) -> None:
        """预热标准协议通道: 选优主机 + 建连 (除权事件走这条)。

        同 prepare: from_best_host 每次都 ping 全部节点, 并发分叉前只调一次。
        """
        self._std_client()

    def fork(self) -> EasyTdxClient:
        """按已选优的主机另开一条连接 (并发拉取用)。

        刻意不走 from_best_host: 那会 ping + 能力探测全部节点(每次数秒),
        16 路并发下不可接受。这里复用 config.json 里已选优的主机直连。

        只复制主连接**已建好**的通道 —— 需要哪条通道就先 prepare 那条:
        prepare() → 行情(fork 后带 MAC); prepare_std() → 除权(fork 后带标准协议)。
        """
        clone = EasyTdxClient()
        if self._mac is not None:
            mac = self._require("easy_tdx.mac.client")
            conn = mac.MacClient()  # host/port/timeout 取缓存的最优主机
            conn.connect()
            clone._mac = conn
        if self._std is not None:
            top = self._require("easy_tdx")
            conn = top.TdxClient()  # host 取 config.json 已选优的 best_host
            conn.connect()
            clone._std = conn
        return clone

    # ---- 行情 ----
    def _guard(self, what: str, fn, *args, **kwargs):
        """统一异常转换: 任何底层异常 → EasyTdxError(任务层据此标记失败/软失败)。"""
        try:
            return fn(*args, **kwargs)
        except EasyTdxError:
            raise
        except Exception as e:
            raise EasyTdxError(f"{what}: {e}") from e

    def kline(self, symbol: str, *, period_name: str, count: int) -> list[dict]:
        """日K/分钟K → [{datetime, open, high, low, close, vol, amount}]。

        adjust 固定 NONE 锁定原始价(前复权由本地因子链投影, 不得用库的复权)。
        vol 单位股(日K 与分钟K 同源同口径), 归一由 provider 负责。
        """
        market, code = parse_symbol(symbol)
        enums = self._require("easy_tdx.mac.enums")
        period = getattr(enums.Period, period_name)
        cap = _MINUTE_MAX_COUNT if period_name.startswith("MIN") else _DAILY_MAX_COUNT
        with self._lock:
            df = self._guard(
                f"K线 {symbol} {period_name}",
                self._mac_client().get_stock_kline,
                market, code, period=period, count=min(int(count), cap),
                adjust=enums.Adjust.NONE,
            )
        if df is None or df.empty:
            return []
        return df[["datetime", "open", "high", "low", "close", "vol", "amount"]].to_dict("records")

    def quotes(self, symbols: list[str], preset: str = "BASIC") -> list[dict]:
        """批量报价 → [{market, code, name, pre_close, open, high, low, close, vol, ...}]。

        单次 ≤80 只由库自动分页。preset 取 PresetField 名(QUOTE 含买一/卖一)。
        """
        if not symbols:
            return []
        pairs = [parse_symbol(s) for s in symbols]
        bitmap = self._require("easy_tdx.codec.bitmap")
        fields = getattr(bitmap.PresetField, preset) + bitmap.PresetField.BASIC
        with self._lock:
            df = self._guard(
                f"报价 {len(pairs)} 只",
                self._mac_client().get_stock_quotes, pairs, fields=fields,
            )
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    def quote_list(self, category_name: str = "A", count: int = 6000) -> list[dict]:
        """市场分类报价列表(全市场一次拉取)。实测 A 股 5580 行 / 5.7 秒。"""
        enums = self._require("easy_tdx.mac.enums")
        category = getattr(enums.Category, category_name)
        with self._lock:
            df = self._guard(
                f"全市场报价 {category_name}",
                self._mac_client().get_stock_quotes_list, category, start=0, count=count,
            )
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    # ---- 除权事件(标准协议) ----
    def xdxr_events(self, symbol: str) -> list[dict]:
        """除权除息事件 → [{date, category, fenhong, peigujia, songzhuangu, peigu}]。

        ★ 单位已定案: 库内 `_normalize_per_10_shares` 已把协议「每 10 股」÷10
        成每股(fenhong/songzhuangu/peigu), 调用方**不得再除 10**。
        peigujia 是配股价(元/股), 不参与该归一。
        """
        market, code = parse_symbol(symbol)
        top = self._require("easy_tdx")
        with self._lock:
            df = self._guard(
                f"除权事件 {symbol}",
                self._std_client().get_xdxr_info, top.Market(market), code,
            )
        if df is None or df.empty:
            return []
        cols = [c for c in ("date", "category", "fenhong", "peigujia", "songzhuangu", "peigu") if c in df.columns]
        return df[cols].to_dict("records")


__all__ = ["EasyTdxClient", "EasyTdxError", "parse_symbol"]