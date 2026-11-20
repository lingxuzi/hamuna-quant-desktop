"""取数守卫 — 用户点名了标的, 模型就必须先取数, 不许开口编数。

复现过的失效: 连续问多只股票时, 第一轮有工具调用, 后面几轮模型看见历史里
上一只股票的完整回答, 就照着那个格式直接续写 —— 一个工具都不调, 价格与
涨跌幅全是编的 (真实 73.02 写成「从 74 元拉到 127.91」)。提示词里
「不重复查询同类信息」还会放大这个倾向, 所以这事只能靠代码兜住, 不能指望
模型自觉。

守卫做两件事:
1. 从用户提问里抽出标的 (代码或名称), 作为本轮必须取数的清单;
2. 记下工具实际取过哪些标的 —— 清单没覆盖完模型就收尾, 先催它回去取数,
   催了还不动就代它取一次, 把真实数据塞回上下文。

判定全在本地, 不依赖模型配合; 催取数与代取数都有上限(与计划纠偏同策略):
到上限就放行, 但明确提示用户相关数字未经取数校验。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.symbols import symbol_of_code

# 催取数上限: 先给模型一次机会自己调工具。
_MAX_NUDGES = 1

# 代取数上限: 催了还不调工具, 就由宿主替它取一次(合成一次工具调用)。
_MAX_FORCED = 2

# 证券代码: 6 位数字, 可带交易所后缀。裸代码靠 known 集合确认存在,
# 否则 "202611" 这类年月数字会被当成深市代码。
_CODE_RE = re.compile(r"(?<![0-9A-Za-z])(\d{6})(?:\.(SH|SZ|BJ))?(?![0-9])", re.IGNORECASE)

# 名称匹配用的最短长度: 单字名称(如 "中")太容易误命中。
_MIN_NAME_LEN = 2

# 指代词: 「它最近怎么样」没点名标的, 但页面上下文里有 —— 那种也得取数。
_REFERENCE_RE = re.compile(r"它|该股|这个标的|这只|此股")


def _compact(text: str) -> str:
    """去掉空白: 维表里的名称带空格 (「五 粮 液」), 用户输入通常不带。"""
    return re.sub(r"\s+", "", text)


def _normalize(value: str) -> str:
    """任意写法的代码 → 标准 symbol (600519 / 600519.sh / SH600519 → 600519.SH)。"""
    text = value.strip().upper()
    if "." in text:
        return text
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        text = text[2:]
    return symbol_of_code(text) or text


@dataclass
class FetchGuard:
    """本轮必须取数的标的清单 + 已取数记录。"""

    asked: set[str] = field(default_factory=set)
    covered: set[str] = field(default_factory=set)
    nudges: int = 0
    forced: int = 0
    _name_to_symbol: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_question(cls, text: str, repo: Any, context_symbol: str = "") -> FetchGuard:
        """从用户提问里抽出标的; repo 不可用时只认带交易所后缀的代码。

        context_symbol 是页面上下文里的标的 —— 提问里用指代词(「它最近怎么样」)
        且没点名任何标的时, 它就是本轮要回答的标的。
        """
        guard = cls()
        if not text:
            return guard
        known: dict[str, str] = {}
        if repo is not None:
            try:
                known = {str(s): str(n) for s, n in repo.get_name_map().items()}
            except Exception:  # noqa: BLE001 — 维表读不到就只按代码识别
                known = {}
        guard._name_to_symbol = {
            _compact(name): symbol for symbol, name in known.items() if name
        }
        guard.asked = _codes_in(text, set(known)) | _names_in(text, guard._name_to_symbol)
        if not guard.asked and context_symbol and _REFERENCE_RE.search(text):
            symbol = _normalize(context_symbol)
            if symbol:
                guard.asked = {symbol}
        return guard

    def record_call(self, args: dict[str, Any]) -> None:
        """记下这次工具调用实际取了哪些标的。"""
        self.covered |= _symbols_in_args(args, self._name_to_symbol)

    def missing(self) -> set[str]:
        """还没取数的标的。"""
        return self.asked - self.covered

    def should_nudge(self) -> bool:
        """本轮是不是该催模型回去取数。"""
        return bool(self.missing()) and self.nudges < _MAX_NUDGES

    def should_force(self) -> bool:
        """催了还不调工具 —— 该由宿主代它取数了。"""
        return bool(self.missing()) and self.forced < _MAX_FORCED

    def mark_forced(self) -> None:
        self.forced += 1

    def nudge_text(self) -> str:
        """催取数的话术。

        末尾「不要复述这条要求」是必须的: 模型很容易把纠偏话术原样搬进正文
        (「您说得对, 我上一轮……」), 用户会莫名其妙 —— 他根本没说过这话。
        """
        symbols = "、".join(sorted(self.missing()))
        return (
            f"下一步: 先调用工具(get_stock_quote / get_stock_daily / get_stock_analysis)拿到 "
            f"{symbols} 的真实数据, 再基于工具返回的内容直接作答。"
            f"只给结论, 不要复述这条要求, 不要凭记忆写价格, 也不要把上一只标的的数字套过来。"
        )

    def notice_text(self) -> str:
        """补取数到上限仍没取到时的提示。"""
        return (
            f"未能取到 {'、'.join(sorted(self.missing()))} 的数据, "
            f"相关数字未经取数校验, 请以行情页面为准。"
        )

    def prompt_addendum(self) -> str:
        """写进 system prompt 的取数清单 —— 让模型第一轮就取对, 少走补取数的弯路。"""
        return (
            "\n\n本轮必须取数的标的: "
            + "、".join(sorted(self.asked))
            + " —— 每个标的都要单独调用工具取数, 不得沿用历史回答或上一只股票的数字。"
        )


def _codes_in(text: str, known: set[str]) -> set[str]:
    """文本里的证券代码; 裸代码必须在 known 里 (排除年月等数字误命中)。"""
    out: set[str] = set()
    for match in _CODE_RE.finditer(text):
        code, suffix = match.group(1), match.group(2)
        if suffix:
            out.add(f"{code}.{suffix.upper()}")
            continue
        symbol = symbol_of_code(code)
        if symbol and symbol in known:
            out.add(symbol)
    return out


def _names_in(text: str, name_to_symbol: dict[str, str]) -> set[str]:
    """文本里的证券名称 (「贵州茅台」); 靠维表反查代码。

    只留最长匹配: 命中「平安银行」时不再把「平安」也算进来(名称互相包含)。
    """
    compact = _compact(text)
    if not compact:
        return set()
    hits = [name for name in name_to_symbol if len(name) >= _MIN_NAME_LEN and name in compact]
    maximal = [
        name for name in hits
        if not any(other != name and name in other for other in hits)
    ]
    return {name_to_symbol[name] for name in maximal}


def _symbols_in_args(args: dict[str, Any], name_to_symbol: dict[str, str]) -> set[str]:
    """工具入参里涉及的标的 (symbol / symbols 两个字段)。"""
    out: set[str] = set()
    raw = args.get("symbol")
    if isinstance(raw, str) and raw.strip():
        out.add(_resolve(raw, name_to_symbol))
    items = args.get("symbols")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str) and item.strip():
                out.add(_resolve(item, name_to_symbol))
    return out


def _resolve(value: str, name_to_symbol: dict[str, str]) -> str:
    """入参可能是代码也可能是名称, 统一解析成 symbol。"""
    text = value.strip()
    if "." not in text and not text.isdigit():
        return name_to_symbol.get(_compact(text), text.upper())
    return _normalize(text)
