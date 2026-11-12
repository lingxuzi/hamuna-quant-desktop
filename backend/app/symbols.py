"""裸股票代码 → 交易所后缀 (SH/SZ/BJ) 的统一判定。

项目里有多处按代码前缀猜交易所: 日K 裸符号补全 (services/kline_sync.py)、
外部数据归一 (services/ext_data.py)。各写一套前缀表会互相漂移 —— 北交所
43/83/87/92 开头的代码曾在 ext_data 里被判成 .SZ。这里只保留一份规则。
"""

from __future__ import annotations

# 前缀 → 交易所, 按顺序匹配 (先长后短, 命中即止):
#   92 → 北交所 920xxx 新代码段, 必须排在 "9" 之前, 否则 900xxx (上交所 B股)
#         会和 920xxx 被同一条 "9" 规则吃掉。
#   4/8 → 北交所 43x / 83x / 87x 存量代码段
#   5/6 → 上交所基金 / 股票; 9 → 上交所 B股; 11/13 → 沪债 / 沪可转债
#   0/1/2/3 → 深交所 (00x 主板, 30x 创业板, 15x/16x/18x 基金与债)
_BJ_PREFIXES = ("92", "4", "8")
_SH_PREFIXES = ("5", "6", "9", "11", "13")
_SZ_PREFIXES = ("0", "1", "2", "3")

_RULES = ((_BJ_PREFIXES, "BJ"), (_SH_PREFIXES, "SH"), (_SZ_PREFIXES, "SZ"))


def exchange_of_code(code: str) -> str | None:
    """6 位裸股票代码 → 交易所后缀 (SH/SZ/BJ); 判不出返回 None。"""
    code = str(code).strip()
    if len(code) != 6 or not code.isdigit():
        return None
    for prefixes, suffix in _RULES:
        if code.startswith(prefixes):
            return suffix
    return None


def symbol_of_code(code: str) -> str | None:
    """6 位裸代码 → 带交易所后缀的完整符号; 判不出交易所返回 None。"""
    suffix = exchange_of_code(code)
    return f"{code}.{suffix}" if suffix else None
