"""取数守卫 — 用户问的是行情/数据, 模型就必须先取数, 不许开口编数。

复现过的失效: 连续问多只股票时, 第一轮有工具调用, 后面几轮模型看见历史里
上一只股票的完整回答, 就照着那个格式直接续写 —— 一个工具都不调, 价格与
涨跌幅全是编的 (真实 73.02 写成「从 74 元拉到 127.91」)。提示词里
「不重复查询同类信息」还会放大这个倾向, 所以这事只能靠代码兜住, 不能指望
模型自觉。

守卫做三件事:
1. 从用户提问里抽出标的 (代码或名称), 作为本轮必须取数的清单;
2. 认「当下行情」类提问 (今天/最近 + 大盘/板块/涨跌) —— 同样必须有市场数据兜底;
3. 记下工具实际取过哪些标的 —— 清单没覆盖完模型就收尾, 先催它回去取数,
   催了还不动就代它取一次, 把真实数据塞回上下文。

判定全在本地, 不依赖模型配合; 催取数与代取数都有上限(与计划纠偏同策略):
到上限还拿不到数, 正文里的数字一律不放行 —— 只交代情况。
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
_REFERENCE_RE = re.compile(r"它|该股|这个标的|这只|此股|这票|这股票|这个票")

# 维表不可用时的兜底: 裸代码看着像年月 (202611) 就不当证券。
_DATE_LIKE_RE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")

# 能给出该标的行情/技术面的工具 —— 只有这些才算「这个标的的行情取过了」。
# get_financials 只给财务数字, 不含价格; 拿它顶行情覆盖等于放过编价格。
_QUOTE_TOOLS = frozenset({"get_stock_quote", "get_stock_daily", "get_stock_analysis"})

# 需要基本面数据的问法。用户要「分析这家公司」时, 财务数据与行情同属取数清单 ——
# 复现过的失效: 模型只调了行情/日K/关键价位, 却在正文里写出整张财务表
# (毛利率 4.98% / ROE -17.95%, 真实值 24.16% / -586.17%), 全是编的。
# 靠正文事后识别不可靠 (截断取数只查行情，覆盖完就放行了), 所以按问法前置要求。
# ponytail: 靠关键词识别「这次要不要财务」, 上限是漏判 —— 换个说法问估值
# (「这票现在贵吗」) 就绕过了。要全覆盖得让模型自报意图, 那时再换口径。
_FUNDAMENTAL_INTENT = (
    "分析", "基本面", "估值", "财务", "财报", "值不值",
    "能买吗", "可以买", "值得买", "贵不贵", "便宜",
)

# 「当下行情」类提问: 问的是市场此刻的状态, 答案里的数字同样必须有数据兜底。
# 复现过的失效: 「今天大盘怎么样」这类问题没点名任何标的, 守卫整个是空的 ——
# 模型可以凭记忆写指数点位、成交额、涨停家数, 一个工具都不调。
# 时间词 + 行情词同时出现才算 —— 「涨停是什么意思」是概念题, 不该按行情问题强制取数。
# 「估值」「走势」不算行情词: 单只票的估值/走势由标的自己的数据兜底。
# ponytail: 靠关键词识别, 上限是漏判 —— 「今天这票怎么走」不含行情词,
# 只在页面给了标的时才能靠指代词兜住; 要全覆盖得让模型自报意图, 那时再换口径。
_CURRENT_WORDS = (
    "今天", "今日", "现在", "目前", "当前", "最近", "近来", "近期", "这两天",
    "本周", "这周", "本月", "今年", "昨天", "昨日", "盘中", "收盘", "开盘",
    "最新", "眼下", "此刻", "这轮",
)
_MARKET_WORDS = (
    "大盘", "市场", "指数", "板块", "行业", "行情", "个股", "股票", "A股",
    "两市", "涨停", "跌停", "涨幅", "跌幅", "成交", "资金", "情绪", "北向",
    "主力", "上证", "深证", "创业板", "科创", "沪指", "深指",
)

# 取数工具: 调过任意一个, 正文里的数字才算有出处。
# 写操作(create_*/sync_*/add_*)与指令加载(list_skills/load_skill*)不算 ——
# 它们不产出市场数据, 拿来当「取过数」等于放行编数字。
_DATA_TOOLS = frozenset({
    "get_stock_quote", "get_stock_daily", "get_stock_analysis", "get_financials",
    "get_watchlist", "get_market_overview", "get_indices", "get_sector_rotation",
    "get_regime", "get_abnormal", "get_lots", "list_signals", "run_strategy",
    "get_factor_values", "check_data_coverage", "query_ext_table",
    "get_sync_status", "list_strategies", "run_backtest",
})

# 市场类取数工具: 「当下行情」类问题必须调过其中之一才算取到数。
_MARKET_TOOLS = frozenset({
    "get_market_overview", "get_indices", "get_sector_rotation",
    "get_regime", "get_abnormal", "get_watchlist",
})

# 用户自己的东西 (策略/信号/因子/持仓/自选): 问这些必须有工具兜底 ——
# 模型完全能凭空写一个「近一年收益率 23%」, 而自己的东西只有查了才知道。
_OWN_DATA_WORDS = ("策略", "信号", "因子", "回测", "持仓", "自选", "组合", "选股")
# 明确在问「我这套东西怎么样」才算 —— 「什么是网格策略」是概念题, 不算。
_OWN_DATA_INTENT = (
    "我的", "帮我", "跑", "看看", "查", "多少", "怎么样", "如何",
    "收益", "回撤", "胜率", "排名", "表现", "有哪些", "哪几", "几个",
)
# 定义类提问: 问的是概念本身, 不该按取数问题强制取数。
_DEFINITION_WORDS = (
    "什么是", "是什么", "定义", "怎么算", "如何计算", "含义", "区别", "什么意思",
)
# 自己数据的取数工具。
_OWN_DATA_TOOLS = frozenset({
    "list_signals", "list_strategies", "run_strategy", "run_backtest",
    "get_factor_values", "get_lots", "get_watchlist", "query_ext_table",
})

# 正文里的「行情数字」: 数字 + 量纲。出现这类数字, 本轮必须有成功的取数调用兜底。
# 只认带量纲的 —— 纯个数(「你有 1 个策略」「看了 3 只票」)不是行情数据。
_MARKET_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|万亿|亿|[万亿]?元|[万亿]?手|[万亿]?股|点|倍|只|家)"
)


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
    # 用户问的是「分析这家公司」这类基本面问题时, 清单里额外要财务数据。
    wants_financials: bool = False
    financials_covered: bool = False
    # 用户问的是「今天大盘怎么样」这类当下行情, 清单里额外要市场数据。
    wants_market: bool = False
    market_covered: bool = False
    # 用户问的是「我的策略最近怎么样」这类自己数据, 清单里额外要自己的数据。
    wants_own_data: bool = False
    own_data_covered: bool = False
    # 本轮成功调用过的取数工具 —— 正文里的数字有没有出处, 看这个。
    data_calls: set[str] = field(default_factory=set)
    nudges: int = 0
    forced: int = 0
    _name_to_symbol: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_question(cls, text: str, repo: Any, context_symbol: str = "") -> FetchGuard:
        """从用户提问里抽出标的。

        repo 的名称维表用来把裸代码和公司名对上; 维表读不到时退化成只按代码规则认
        (见 _codes_in) —— 守卫失灵比误认更糟。

        context_symbol 是页面上下文里的标的 —— 提问里用指代词(「它最近怎么样」)
        且没点名任何标的时, 它就是本轮要回答的标的。
        """
        guard = cls()
        if not text:
            return guard
        known: dict[str, str] = {}
        name_map_broken = False
        if repo is not None:
            try:
                known = {str(s): str(n) for s, n in repo.get_name_map().items()}
            except Exception:  # noqa: BLE001 — 维表读失败: 退化成按代码规则认 (见 _codes_in)
                known = {}
                name_map_broken = True
        guard._name_to_symbol = {
            _compact(name): symbol for symbol, name in known.items() if name
        }
        guard.asked = (
            _codes_in(text, set(known), allow_bare=name_map_broken)
            | _names_in(text, guard._name_to_symbol)
        )
        if not guard.asked and context_symbol and _REFERENCE_RE.search(text):
            symbol = _normalize(context_symbol)
            if symbol:
                guard.asked = {symbol}
        # 基本面口径只管个股: 指数/ETF 没有三张表, 要求了也取不到。
        guard.wants_financials = bool(guard.asked) and any(
            word in text for word in _FUNDAMENTAL_INTENT
        ) and not _only_non_stock(guard.asked, repo)
        guard.wants_market = _is_market_question(text) and not guard.asked
        guard.wants_own_data = _is_own_data_question(text)
        return guard

    def record_call(self, name: str, args: dict[str, Any]) -> None:
        """记下这次工具调用实际取了哪些标的。

        行情与财务分开记账: get_financials 不含价格, 拿它当行情覆盖等于放过编价格。
        """
        if name in _DATA_TOOLS:
            self.data_calls.add(name)
        if name in _MARKET_TOOLS:
            self.market_covered = True
        if name in _OWN_DATA_TOOLS:
            self.own_data_covered = True
        if name == "get_financials":
            self.financials_covered = True
            return
        if name in _QUOTE_TOOLS:
            self.covered |= _symbols_in_args(args, self._name_to_symbol)

    def missing(self) -> set[str]:
        """还没取数的标的。"""
        return self.asked - self.covered

    def missing_financials(self) -> bool:
        """问的是个股基本面, 但一轮下来没调 get_financials。"""
        return self.wants_financials and not self.financials_covered

    def missing_market(self) -> bool:
        """问的是当下行情, 但一轮下来没调过市场类取数工具。"""
        return self.wants_market and not self.market_covered

    def missing_own_data(self) -> bool:
        """问的是自己策略/持仓, 但一轮下来没调过自己数据的工具。"""
        return self.wants_own_data and not self.own_data_covered

    def needs_data(self) -> bool:
        """本轮问的是需要拿数据回答的问题吗。

        具名标的、当下行情、自己的策略持仓都算。概念题(「涨停是什么意思」)不算 ——
        那类回答本来就不该有行情数字, 拦了只会让用户什么都拿不到。
        """
        return bool(self.asked) or self.wants_market or self.wants_own_data

    def unverified_numbers(self, text: str) -> bool:
        """正文里的行情数字没有出处吗。

        本轮一次取数工具都没成功调用过, 正文里却出现了带量纲的数字 —— 这些数字
        是模型凭记忆写的, 不能给用户看。
        """
        if self.data_calls:
            return False
        if not _MARKET_NUMBER_RE.search(text or ""):
            return False
        return self.needs_data()

    def should_nudge(self, text: str = "") -> bool:
        """本轮是不是该催模型回去取数。"""
        if self.nudges >= _MAX_NUDGES:
            return False
        return self.incomplete() or self.unverified_numbers(text)

    def should_force(self) -> bool:
        """催了还不调工具 —— 该由宿主代它取数了 (行情与财务各能代取一次)。"""
        return self.incomplete() and self.forced < _MAX_FORCED

    def force_call(self) -> tuple[str, dict[str, Any]] | None:
        """该代模型取什么: (工具名, 入参)。

        先补行情, 再补财务 —— 财务得逐个标的取, 这里只代取第一个。
        """
        missing = sorted(self.missing())
        if missing:
            return "get_stock_quote", {"symbols": missing}
        if self.missing_financials() and self.asked:
            return "get_financials", {"symbol": sorted(self.asked)[0], "table": "metrics", "periods": 4}
        if self.missing_market():
            return "get_market_overview", {}
        return None

    def mark_forced(self) -> None:
        self.forced += 1

    def incomplete(self) -> bool:
        """清单没覆盖完: 还有标的没取数, 该取财务没取, 该取市场/自己的数据没取。"""
        return (
            bool(self.missing())
            or self.missing_financials()
            or self.missing_market()
            or self.missing_own_data()
        )

    def nudge_text(self) -> str:
        """催取数的话术。

        末尾「不要复述这条要求」是必须的: 模型很容易把纠偏话术原样搬进正文
        (「您说得对, 我上一轮……」), 用户会莫名其妙 —— 他根本没说过这话。
        """
        parts: list[str] = []
        symbols = "、".join(sorted(self.missing()))
        if symbols:
            parts.append(
                f"调用 get_stock_quote / get_stock_daily / get_stock_analysis 拿到 "
                f"{symbols} 的真实行情"
            )
        if self.missing_financials():
            targets = "、".join(sorted(self.asked))
            parts.append(f"调用 get_financials 取 {targets} 的财务数据")
        if self.missing_market():
            parts.append(
                "调用 get_market_overview / get_indices / get_sector_rotation / get_regime "
                "取当下的真实市场数据"
            )
        if self.missing_own_data():
            parts.append(
                "调用 list_signals / list_strategies / run_strategy / get_lots / get_watchlist "
                "取你自己的策略与持仓数据"
            )
        body = "; ".join(parts)
        tail = "不要凭记忆写价格与财务数字"
        if self.missing_market():
            tail = "不要凭记忆写指数点位、成交额、涨跌幅"
        return (
            f"下一步: 先{body}, 然后基于工具返回的内容直接作答。"
            f"只给结论, 不要复述这条要求, {tail}, "
            f"也不要把上一只标的的数字套过来。"
        )

    def notice_text(self) -> str:
        """补取数到上限仍没取到时的提示。"""
        gaps: list[str] = []
        if self.missing():
            gaps.append(f"未能取到 {'、'.join(sorted(self.missing()))} 的行情数据")
        if self.missing_financials():
            gaps.append("未能取到财务数据")
        if self.missing_market():
            gaps.append("未能取到市场数据")
        if self.missing_own_data():
            gaps.append("未能取到策略/持仓数据")
        return f"{'、'.join(gaps)}, 相关数字未经取数校验, 请以行情/财务页面为准。"

    def blocked_notice(self) -> str:
        """一个取数工具都没成功调过时的提示 —— 正文已拦下, 只交代情况。"""
        return (
            "本轮没有调用取数工具, 正文里的数字没有数据出处, 已拦下没有交付。"
            "可点击重试, 或换个问法 (指名标的, 或说明要看哪个市场维度)。"
        )

    def prompt_addendum(self) -> str:
        """写进 system prompt 的取数清单 —— 让模型第一轮就取对, 少走补取数的弯路。"""
        parts: list[str] = []
        if self.asked:
            parts.append(
                "\n\n本轮必须取数的标的: "
                + "、".join(sorted(self.asked))
                + " —— 每个标的都要单独调用 get_stock_quote / get_stock_daily / get_stock_analysis "
                "取真实行情, 不得沿用历史回答或上一只股票的数字。"
            )
            if self.wants_financials:
                parts.append(
                    " 用户问的是个股基本面/整体分析, 必须再调用 get_financials 取真实财务数据; "
                    "没有调用过就不要写毛利率、ROE、负债率、EPS 等任何财务数字。"
                )
        if self.wants_market:
            parts.append(
                " 用户问的是当下行情/板块/涨跌, 必须调用 get_market_overview / get_indices / "
                "get_sector_rotation / get_regime 取真实数据; 没调用过就不要写指数点位、"
                "成交额、涨跌幅、涨停家数。"
            )
        if self.wants_own_data:
            parts.append(
                " 用户问的是自己的策略/信号/持仓, 必须调用 list_signals / list_strategies / "
                "run_strategy / get_lots / get_watchlist 取真实数据; 没调用过就不要写收益率、"
                "回撤、胜率、持仓成本等任何数字。"
            )
        return "".join(parts)


def _is_market_question(text: str) -> bool:
    """问的是市场此刻的状态吗 (时间词 + 行情词)。

    两者同时出现才算 —— 「涨停是什么意思」是概念题, 不该按行情问题强制取数,
    更不该因为答案里的「10%」被拦下。
    """
    return (
        any(word in text for word in _CURRENT_WORDS)
        and any(word in text for word in _MARKET_WORDS)
    )


def _is_own_data_question(text: str) -> bool:
    """问的是用户自己的策略/信号/持仓这类数据吗。

    模型完全能凭空写一句「近一年收益率 23%」—— 自己的东西只有查了才知道。
    ponytail: 靠关键词识别, 上限是漏判 —— 「我那套打法最近如何」这类问法绕得过;
    要全覆盖得让模型自报意图, 那时再换口径。
    """
    if any(word in text for word in _DEFINITION_WORDS):
        return False
    if not any(word in text for word in _OWN_DATA_WORDS):
        return False
    return any(word in text for word in _OWN_DATA_INTENT) or any(
        word in text for word in _CURRENT_WORDS
    )


def _only_non_stock(symbols: set[str], repo: Any) -> bool:
    """清单里是不是全是指数/ETF (没有财务三张表)。repo 不可用时一律按个股算。"""
    if repo is None:
        return False
    try:
        return all(repo.resolve_asset_type(s) != "stock" for s in symbols)
    except Exception:  # noqa: BLE001 — 判定不了就按个股要求财务, 宁可多催一次
        return False


def _codes_in(text: str, known: set[str], allow_bare: bool = False) -> set[str]:
    """文本里的证券代码。

    裸代码优先认维表里的 (维表能排除掉年月等数字误命中); 维表读取失败时
    (allow_bare) 退而求其次按代码规则认, 只把像年月的挡掉 ——
    守卫整个失灵比误认一个标的更糟, 误认的后果是多取一次数, 失灵的后果是模型编数字。
    """
    out: set[str] = set()
    for match in _CODE_RE.finditer(text):
        code, suffix = match.group(1), match.group(2)
        if suffix:
            out.add(f"{code}.{suffix.upper()}")
            continue
        symbol = symbol_of_code(code)
        if not symbol:
            continue
        if symbol in known or (allow_bare and not _DATE_LIKE_RE.match(code)):
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
