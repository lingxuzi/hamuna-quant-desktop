"""近 120 天市场事实 → AI 策略决策 (阈值 / 基础过滤 / 风控)。

只决定「能被校验的参数」; 因子池与权重方向来自挖掘冠军, 由平台渲染,
不由 AI 写入 (见 app/strategy/factor_rank_render.py)。
"""
from __future__ import annotations

import math

from app.strategy.custom_signals_ai import _extract_json_object

_SYSTEM_PROMPT = """你是一位 A 股量化研究员。根据给定的近 120 天市场事实与挖掘出的因子池,
为因子排名策略确定参数。只输出一个 JSON 对象, 不要任何解释。

字段:
- name: 策略名 (不超过 20 字, 体现当前市场特征)
- description: 一句话说明 (不超过 60 字)
- params: {entry_score: 0-100, exit_score: 0-100, top_rank: 1-100}, entry_score 必须大于 exit_score
- basic_filter: {price_min: 元, market_cap_min: 元, amount_min: 元}, 未定项省略
- risk: {stop_loss: 负数小数如 -0.06, max_hold_days: 整数天数}

要求: 市场弱势/情绪冰点时收紧入场分、缩短持有、从严止损; 市场强势/主升时
放宽入场分、放大持有天数。不要编造数据里没有的事实。"""

_PARAM_BOUNDS = {
    "entry_score": (0.0, 100.0),
    "exit_score": (0.0, 100.0),
    "top_rank": (1, 100),
}
_BASIC_BOUNDS = {
    "price_min": (0.0, 10000.0),
    "price_max": (0.0, 10000.0),
    "market_cap_min": (0.0, 1e14),
    "amount_min": (0.0, 1e12),
    "turnover_min": (0.0, 100.0),
}
_DEFAULT_RISK = {"stop_loss": -0.08, "max_hold_days": 30}


def _stat(block: dict, key: str) -> tuple:
    """取 {mean, last} 统计块 → (mean, last); 缺键返回 (None, None)。

    事实结构与消费端解耦: build_market_facts 新增/改名一个统计块时, 这里少一个
    键就会让整条 AI 策略生成路径抛 KeyError 变成 error 事件 (用户只看到失败)。
    缺项显示「无」, 其余统计照常喂给模型。
    """
    entry = block.get(key) or {}
    return entry.get("mean"), entry.get("last")

def _fmt(value) -> str:
    if value is None:
        return "无"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _stat_line(block: dict, key: str, label: str) -> list[str]:
    mean, last = _stat(block, key)
    return [f"{label}均值 {_fmt(mean)} / 最新 {_fmt(last)}"]

def _market_block(facts: dict) -> str:
    m = facts["market"]
    states = ", ".join(f"{s['state']} {s['days']}天" for s in m["states"])
    phases = ", ".join(f"{p['phase']} {p['days']}天" for p in m["phases"]) or "无"
    return "\n".join([
        "【大盘】",
        f"交易日数: {m['bars']}",
        f"状态分布: {states}",
        f"情绪阶段: {phases}; 最新阶段: {_fmt(m['phase_last'])}",
        *_stat_line(m, "score", "评分"),
        # up_ratio 是 up/down 的比值 (无上界), 不是占比。喂给模型必须用 up_share
        # (up/(up+down), 0-1); 写 "比值 0.27" 会被理解成「27% 上涨」, 方向反了。
        *_stat_line(m, "up_share", "上涨家数占比"),
        *_stat_line(m, "avg_pct", "全市场平均涨幅"),
        *_stat_line(m, "above_ma20_pct", "站上 20 日均线占比"),
    ])


def _sector_block(facts: dict) -> str:
    s = facts["sector"]
    rows = "\n".join(
        f"- {x['member']}: 上榜 {x['days']} 天, 平均强度 {_fmt(x['avg_score'])}, "
        f"最佳排名 {x['best_rank']}, 连板合计 {x['limit_up_sum']}, "
        f"前后半程强度变化 {_fmt(x['momentum'])}"
        for x in s["sectors"]
    )
    rising = "、".join(x["member"] for x in s["rising"]) or "无"
    fading = "、".join(x["member"] for x in s["fading"]) or "无"
    return f"【板块】\n强势板块:\n{rows}\n走强中: {rising}\n走弱中: {fading}"


def _money_flow_block(facts: dict) -> str:
    rows = "\n".join(
        f"- {t['table']}·{t['field']}: 样本 {t['points']}, 均值 {_fmt(t['mean'])}, "
        f"最新 {_fmt(t['last'])}, 近 20 期对比前 20 期 {_fmt(t['recent_vs_earlier'])}"
        for t in facts["money_flow"]["tables"]
    )
    return f"【资金流】\n{rows}"


_BLOCKS = {
    "market": _market_block,
    "sector": _sector_block,
    "money_flow": _money_flow_block,
}


def build_messages(
    facts: dict,
    *,
    factors: dict[str, float],
    directions: dict[str, str],
    asset_type: str,
) -> list[dict]:
    """facts: build_market_facts 的返回; factors/directions: 挖掘冠军的因子池。"""
    sections = [_BLOCKS[name](facts) for name in facts["dimensions"] if name in _BLOCKS]
    pool = "\n".join(
        f"- {name}: 权重 {weight:.3f}, 方向 {'越高越好' if directions.get(name) == 'high' else '越低越好'}"
        for name, weight in factors.items()
    )
    universe = "ETF" if asset_type == "etf" else "个股"
    window = facts["window"]
    user = (
        f"观察窗口: {window['start']} 至 {window['end']} (共 {window['days']} 天自然日)\n"
        f"标的: {universe}\n\n"
        + "\n\n".join(sections)
        + f"\n\n【挖掘出的因子池】(权重方向已固定, 不要修改)\n{pool}\n\n"
        "请据此给出优先策略参数 (JSON)。"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _clamp(value: object, name: str, bounds: tuple[float, float]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    low, high = bounds
    return min(max(number, low), high)


def parse_and_validate(text: str, *, factors: dict[str, float]) -> dict:
    """把 AI 输出归一为可渲染的决策; 越界值夹逼, 缺项用安全默认, 不报错。"""
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回的策略决策不是对象")

    params: dict = {}
    for name, bounds in _PARAM_BOUNDS.items():
        raw = (parsed.get("params") or {}).get(name) if isinstance(parsed.get("params"), dict) else None
        value = _clamp(raw, name, bounds)
        if value is None:
            value = {"entry_score": 70.0, "exit_score": 40.0, "top_rank": 20}[name]
        params[name] = int(value) if name == "top_rank" else value
    if params["entry_score"] <= params["exit_score"]:
        params["entry_score"] = min(100.0, params["exit_score"] + 10.0)

    # 因子数不足时 top_rank 无意义, 收敛到实际可评分的数量
    params["top_rank"] = max(1, min(int(params["top_rank"]), max(len(factors), 1) * 5))

    basic_filter = {}
    raw_filter = parsed.get("basic_filter") if isinstance(parsed.get("basic_filter"), dict) else {}
    for name, bounds in _BASIC_BOUNDS.items():
        value = _clamp(raw_filter.get(name), name, bounds)
        if value is not None:
            basic_filter[name] = value

    risk = dict(_DEFAULT_RISK)
    raw_risk = parsed.get("risk") if isinstance(parsed.get("risk"), dict) else {}
    stop = _clamp(raw_risk.get("stop_loss"), "stop_loss", (-0.5, -0.01))
    if stop is not None:
        risk["stop_loss"] = stop
    hold = _clamp(raw_risk.get("max_hold_days"), "max_hold_days", (1, 10000))
    if hold is not None:
        risk["max_hold_days"] = int(hold)

    name = str(parsed.get("name") or "").strip()[:20] or None
    description = str(parsed.get("description") or "").strip()[:60]
    return {
        "name": name,
        "description": description,
        "params": params,
        "basic_filter": basic_filter,
        "risk": risk,
    }
