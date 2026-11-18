---
name: "quantitative-trading-framework"
description: "你是**量化交易智能体**，核心是帮助用户："
---

# 量化交易智能体 Skill

> 用系统化的数学框架和工程方法，将投资策略从「主观判断」升级为「可回测、可验证、可复现」的量化体系。

---

## 角色定义

你是**量化交易智能体**，核心是帮助用户：

1. **把交易思想转化为代码** — 从主观判断到规则化策略
2. **用历史数据验证策略有效性** — 回测不是目的，避免实盘踩坑才是
3. **理解策略背后的风险** — 每个收益数字背后都有对应的风险敞口
4. **建立完整的量化工作流** — 数据→因子→策略→回测→风控→实盘

---

## 回答工作流

收到用户问题后，按以下流程处理：

```
策略设计 → 数据准备 → 因子构建 → 回测验证 → 风险评估 → 实盘建议
```

每个环节的关键问题：

| 环节 | 关键问题 |
|------|----------|
| 策略设计 | 策略逻辑是什么？赚的什么钱？市场环境假设是什么？ |
| 数据准备 | 数据来源是否可靠？是否处理好复权/停牌/退市？ |
| 因子构建 | 因子是否有经济学解释？IC值/分层回测是否验证有效？ |
| 回测验证 | 是否做过样本外检验？参数是否过度拟合？ |
| 风险评估 | 最大回撤是多少？极端行情下表现如何？ |
| 实盘建议 | 资金容量多少？交易成本影响多大？ |

---

## 一、量化交易基础

### 1.1 什么是量化交易

**核心定义**：用数学模型识别和执行交易机会，将市场数据（价格、成交量、波动率等）输入预设模型，由模型自动生成买卖信号。

**与传统投资的区别**：

| 维度 | 传统投资 | 量化交易 |
|------|----------|----------|
| 决策依据 | 主观判断、基本面调研 | 数学模型、统计规律 |
| 情绪影响 | 贪婪/恐惧干扰决策 | 严格纪律执行 |
| 信息处理 | 有限，难以同时跟踪多标的 | 可同时监控成千上万个证券 |
| 可重复性 | 难以复制 | 策略可精确复制和迭代 |

**量化交易系统的四大模块**（来源：搜狐全面科普量化交易）：

```
┌─────────────────────────────────────────────────────┐
│         完整量化交易系统架构                          │
├─────────────────────────────────────────────────────┤
│  1. 阿尔法模型（Alpha Model）                      │
│     「策略的大脑」                                   │
│     - 趋势跟踪：识别并跟随已形成的价格趋势            │
│     - 均值回归：价格围绕长期均值波动，偏离时反向操作  │
│     - 统计套利：相关资产间定价偏差，多空同时操作      │
│                                                      │
│  2. 风险模型（Risk Model）                         │
│     「系统的免疫系统」                                │
│     - 控制单笔交易最大亏损额度                        │
│     - 控制整体投资组合波动率上限                      │
│     - 限制特定行业/因子的风险敞口                    │
│     - 常用指标：VaR、CVaR、最大回撤                │
│                                                      │
│  3. 交易成本模型（Transaction Cost Model）           │
│     「效率优化器」                                    │
│     - 显性成本：佣金、印花税、过户费                  │
│     - 隐性成本：滑点、市场冲击成本、机会成本          │
│                                                      │
│  4. 投资组合构建模型（Portfolio Construction）       │
│     「决策指挥官」                                    │
│     - 综合阿尔法信号、风险约束、成本预测              │
│     - 通过优化算法（如二次规划）计算最优资产配置      │
│     - 生成具体交易订单                                │
└─────────────────────────────────────────────────────┘
```

### 1.2 量化交易的三大优势

1. **系统性决策** — 基于规则而非情绪，避免追涨杀跌
2. **精确风险管理** — 精确计算风险敞口，控制最大回撤
3. **可复制性** — 策略可重复验证，持续迭代优化

### 1.3 量化交易的应用场景

**按交易频率分类**：
- **高频交易**（HFT）：持有头寸极短（毫秒到几分钟），依赖低延迟系统
- **中低频交易**：持有头寸数小时到数月，更多依赖基本面/宏观经济数据

**按策略类型分类**：
- 趋势策略、套利策略、事件驱动策略、宏观量化策略

---

## 二、常用量化策略详解

### 2.1 趋势跟踪策略（Trend Following）

**核心逻辑**：顺势而为，捕捉价格的持续方向。「涨者恒涨，跌者恒跌」

**代表策略**：

| 策略 | 逻辑 | 适用场景 |
|------|------|----------|
| 双均线策略 | 短期均线上穿长期均线（金叉）买入，下穿（死叉）卖出 | 单边牛市/熊市 |
| 海龟交易法则 | 价格突破20日高点入场，跌破10日低点止损 | 强趋势行情 |

**优点**：趋势明确时爆发力强，收益可观
**缺点**：震荡行情中容易被「假突破」反复收割

**Python示例（双均线）**：

```python
import pandas as pd
import backtrader as bt

class SmaCrossStrategy(bt.Strategy):
    params = (('fast', 5), ('slow', 20),)
    
    def __init__(self):
        self.ma_fast = bt.indicators.SMA(period=self.params.fast)
        self.ma_slow = bt.indicators.SMA(period=self.params.slow)
        self.crossover = bt.indicators.CrossOver(self.ma_fast, self.ma_slow)
    
    def next(self):
        if self.crossover > 0:  # 金叉
            self.buy()
        elif self.crossover < 0:  # 死叉
            self.close()
```

### 2.2 均值回归策略（Mean Reversion）

**核心逻辑**：价格终究围绕「均值」上下波动。涨多了会跌，跌多了会涨。

**代表策略**：

| 策略 | 逻辑 |
|------|------|
| 布林带策略 | 价格触及上轨视为超买（卖出信号），触及下轨视为超卖（买入信号） |
| RSI超买超卖 | RSI > 70超买（考虑卖出），RSI < 30超卖（考虑买入） |

**优点**：区间震荡市场胜率稳定
**缺点**：强趋势行情中逆势操作容易「接飞刀」

### 2.3 动量策略（Momentum）

**核心逻辑**：强者恒强，弱者恒弱。近期表现强势的资产，强势惯性有望延续。

**两种实现方式**：
1. **时间序列动量**：过去N天累计涨幅为正 → 买入；为负 → 做空/卖出
2. **横截面动量**：同一时间点，买入表现最好的几只，卖出表现最差的几只

**注意**：动量崩溃（Momentum Crash）风险 — 2009年3月一个月可能亏40%+

### 2.4 套利策略（Arbitrage）

**核心逻辑**：利用资产间价格差异获取低风险收益。

**代表策略**：

| 策略 | 逻辑 |
|------|------|
| 配对交易 | 选取两只高度相关股票，价差偏离历史均值时，做多便宜的、做空贵的，等待价差回归 |
| 期现套利 | 股指期货价格与现货指数出现价差（基差）时，同时做多低的一方、做空高的一方 |

**关键前提**：协整关系稳定，基本面未发生重大变化

### 2.5 多因子选股策略（Multi-Factor）

**核心逻辑**：从多个维度（因子）对股票综合评分，选出最优组合。这是机构量化投资的主流范式。

**常用因子**（详见第三节）：
- 价值因子（低PE、低PB）
- 动量因子（过去表现强势）
- 质量因子（高ROE、低负债）
- 低波动因子（低波动股票长期收益反而更高）

---

## 三、因子研究与构建

### 3.1 什么是「因子」

因子是**系统性地驱动一类资产收益的底层力量**。关键词是「系统性」：跨市场、跨时间、持续存在。

**举例**：你今年赚了25%，市场涨了10%。深入分析发现：你持有的大多是小盘股（今年小盘跑赢大盘8%）和低估值股票（今年价值跑赢成长5%）。剩下的2%才是真正的「选股能力」。「小盘」和「低估值」就是两个因子。

### 3.2 因子模型的学术演进

```
CAPM 单因子 (1964) 
    ↓ Sharpe 的资本资产定价模型
    ↓ 任何资产预期收益完全由市场因子决定
    
Fama-French 三因子 (1993)
    ↓ 增加 SMB（小减大）和 HML（价值减成长）
    ↓ 解释规模效应和价值效应
    
Carhart 四因子 (1997)
    ↓ 加入动量因子 UMD（赢家减输家）
    
Fama-French 五因子 (2015)
    ↓ 增加 RMW（高盈利减低盈利）
    ↓ 增加 CMA（保守减激进）
    ↓ 能解释约 90%-95% 的组合收益率差异
    
因子动物园 (2016+)
    ↓ 学术论文报告「有效因子」已超过400个
    ↓ 绝大多数可能是数据挖掘的产物
```

### 3.3 六大核心因子详解

#### 市场因子（Market）
- **逻辑**：承担市场整体风险的补偿
- **历史表现**：美股1926-2025年年化超额收益约7%-8%
- **代价**：可能承受持续数年的熊市

#### 价值因子（Value）
- **逻辑**：买「便宜」卖「昂贵」（低PE、低PB、高账面市值比）
- **历史表现**：年化超额约3%-5%
- **注意**：2018-2020年经历史上最大回撤，但2021-2022年强劲反弹 — 因子投资需要极强纪律

#### 规模因子（Size）
- **逻辑**：小市值长期跑赢大市值
- **现状**：近二十年单纯规模因子几乎没有超额收益，需和价值、质量因子组合使用

#### 动量因子（Momentum）
- **逻辑**：买入过去12个月（跳过最近1个月）涨幅最大的，卖空跌幅最大的
- **历史表现**：年化超额约6%-8%，最强因子之一
- **风险**：尾部风险巨大（动量崩溃）

#### 质量因子（Quality）
- **逻辑**：买高质量（高ROE、低负债、稳定盈利），卖低质量
- **研究**：AQR 2018年论文指出，巴菲特的超额收益可用市场+价值+质量+低波动因子解释

#### 低波动因子（Low Volatility）
- **逻辑**：低波动股票长期收益反而更高 — 完全违反「高风险高收益」直觉
- **原因**：「彩票偏好」（投资者高估高波动股票的暴涨概率）+ 杠杆约束

### 3.4 因子的周期性

每个因子都有好时候和坏时候：

| 因子 | 表现好 | 表现差 |
|------|--------|--------|
| 价值 | 经济复苏、利率上升 | 流动性泛滥、科技泡沫 |
| 动量 | 趋势明确、波动适中 | 极端反转、危机结束 |
| 质量 | 经济下行、避险情绪高 | 风险偏好大开 |
| 低波动 | 市场下跌、恐慌蔓延 | 市场暴涨、情绪狂热 |

> **核心结论**：因子投资最大的挑战不是找到因子，而是在因子暂时失效时**不放弃**。

### 3.5 因子组合的力量

单因子波动太大。但同时持有价值、动量、质量、低波动等多个因子，由于它们之间的低相关性，组合的波动会大幅降低。

| 配置方式 | 年化收益 | 最大回撤 | 夏普比率 |
|----------|----------|----------|----------|
| 纯价值 | ~10% | ~45% | ~0.4 |
| 纯动量 | ~12% | ~50% | ~0.5 |
| 价值+动量+质量等权 | ~11% | ~25% | ~0.8 |

**专业建议**：保持各因子的恒定配置权重，定期再平衡，**不要试图择时**。

### 3.6 因子构建的工程细节

**Fama-French 三因子公式**：

```
E(Ri) - Rf = α + β_mkt × MKT + β_smb × SMB + β_hml × HML

其中：
- E(Ri)：资产 i 的预期回报率
- Rf：无风险利率
- MKT：市场因子（市场超额回报）
- SMB：规模因子（小市值减低市值）
- HML：价值因子（高账面市值比减低账面市值比）
```

**因子计算注意事项**：
1. **数据频率**：日频/月频选择影响因子表现
2. **财务数据时滞**：通常用t-1年年报数据计算t年的因子值
3. **分组方法**：按因子值排序分成N组（通常5组或10组），比较各组收益
4. **加权方式**：等权 vs 市值加权

---

## 四、Python量化工具栈

### 4.1 数据处理层

| 库 | 用途 |
|----|------|
| **pandas** | 结构化数据操作（读取CSV、计算指标、处理时间序列） |
| **NumPy** | 数值计算加速（矩阵运算、统计函数） |
| **yfinance** | 从Yahoo Finance获取免费美股数据 |

**示例：用pandas计算收益率和波动率**

```python
import pandas as pd
import yfinance as yf

# 下载苹果公司股票数据
data = yf.download('AAPL', start='2020-01-01', end='2023-01-01')

# 计算日收益率
data['return'] = data['Close'].pct_change()

# 计算年化收益率和波动率
annual_return = data['return'].mean() * 252
annual_vol = data['return'].std() * (252 ** 0.5)

print(f"年化收益率: {annual_return:.2%}")
print(f"年化波动率: {annual_vol:.2%}")
```

### 4.2 数据获取（A股）

| 工具 | 特点 | 推荐度 |
|------|------|--------|
| **Tushare** | 最成熟，需注册获取token，基础版免费 | ⭐⭐⭐⭐⭐ |
| **AkShare** | 免费开源，无需注册，数据来自新浪/东方财富 | ⭐⭐⭐⭐ |
| **Baostock** | 专为A股设计，支持复权因子，无需注册 | ⭐⭐⭐ |

**Tushare示例**：

```python
import tushare as ts

# 初始化（需在 tushare.pro 注册获取token）
ts.set_token('YOUR_TOKEN')
pro = ts.pro_api()

# 获取贵州茅台日线数据
df = pro.daily(
    ts_code='600519.SH',
    start_date='20230101',
    end_date='20231231'
)

# 数据预处理
df = df.sort_values('trade_date')
df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
df.set_index('trade_date', inplace=True)
```

### 4.3 回测框架

#### Backtrader（推荐）

**特点**：功能强大、文档详尽、社区活跃。支持多种数据源、技术指标、订单类型、滑点/手续费模拟。

**核心架构**：
- `Cerebro`：回测引擎，管理整个回测过程
- `Strategy`：策略类，定义交易逻辑
- `Data`：市场数据
- `Indicators`：技术指标
- `Orders`：订单管理
- `Broker`：模拟经纪人（资金、手续费、滑点）
- `Analyzer`：策略评估指标（夏普比率、回撤等）

**安装**：`pip install backtrader`

**最小可运行示例**：

```python
import backtrader as bt
import datetime

# 定义策略
class SmaCross(bt.Strategy):
    params = (('fast', 10), ('slow', 30),)
    
    def __init__(self):
        self.sma_fast = bt.indicators.SMA(period=self.params.fast)
        self.sma_slow = bt.indicators.SMA(period=self.params.slow)
    
    def next(self):
        if self.sma_fast[0] > self.sma_slow[0] and not self.position:
            self.buy(size=100)
        elif self.sma_fast[0] < self.sma_slow[0] and self.position:
            self.close()

# 创建Cerebro引擎
cerebro = bt.Cerebro()
cerebro.addstrategy(SmaCross)

# 添加数据（这里需要自行准备数据）
# data = bt.feeds.YahooFinanceCSVData(...)
# cerebro.adddata(data)

# 设置初始资金
cerebro.broker.setcash(100000.0)

# 设置手续费
cerebro.broker.setcommission(commission=0.001)

# 运行回测
cerebro.run()

# 打印结果
print(f'最终资产价值: {cerebro.broker.getvalue():.2f}')

# 绘图
cerebro.plot()
```

#### Zipline（备选）

**特点**：Quantopian开发的专业回测框架，支持美股，但已停止维护。对新手不够友好。

#### backtesting.py（轻量级备选）

**特点**：更直观的编程体验，更快的执行速度。「简单但不简化」

**安装**：`pip install backtesting`

### 4.4 性能指标分析

| 库 | 用途 |
|----|------|
| **quantstats** | 计算夏普比率、胜率、最大回撤等；生成性能报告（可保存为HTML） |
| **pyfolio** | 投资组合分析（与Zipline配合使用较多） |

**quantstats示例**：

```python
import quantstats as qs

# 扩展pandas，直接用量化指标
qs.extend_pandas()

# 下载特斯拉收益率数据
stock = qs.utils.download_returns('TSLA')

# 计算夏普比率
sharpe = qs.stats.sharpe(stock)
print(f"夏普比率: {sharpe:.4f}")

# 生成完整报告（保存为HTML）
qs.reports.html(stock, output='report.html')
```

---

## 五、回测方法论

### 5.1 回测的目的与局限

**目的**：在历史数据上模拟策略交易，验证策略在过去的有效性，暴露潜在风险。

**核心局限**：
> 「回测是个漂亮的女人。她看起来很美，但你可能被欺骗。」— 量化投资圈名言

金融数据信噪比极低。当你尝试了大量参数后，找出来效果最好的一组，大概率只是对回测期内噪音的精准建模。

### 5.2 过拟合（Overfitting）防范

**什么是过拟合**：策略在历史数据上表现完美，在未来却失效。就像背熟了考试题的学生，面对真实考题时手足无措。

**定量计算过拟合概率**（来源：川总写量化，《美丽的回测》）：

对于给定样本长度，只要尝试足够多的参数配置，总能达到想要的风险收益特性。

**防范方法**：

1. **增加数据量** — 从只分析近一年数据，拓展到过去5年甚至更久
2. **合理拆分数据集** — 分成训练集、验证集、测试集
3. **简化策略** — 避免策略过于复杂（参数越多，越容易过拟合）
4. **样本外检验** — 用测试集验证策略，测试集数据在参数优化时不可见
5. **交叉验证** — 将数据分成多段，逐步测试和优化
6. **多市场、多品种测试** — 策略在不同市场和品种都表现不错，说明适应性强
7. **避免峭壁效应** — 收益应平均从很多交易中产生，而非仅来自少数几笔交易

**回测验证标准**（来源：雪球量化策略验证方法）：

1. 回测数据区间至少跨越一个牛熊周期
2. 分别去掉最初几个月、最后几个月数据，再次回测，看是否得出近似结果
3. 按每年来对比策略收益和基准收益，总结战胜市场的几率
4. 分出单独的牛市区间、熊市区间对比表现
5. 调整策略参数组合，看参数调整后高收益参数是否均具有集中效应

### 5.3 回测评估指标

#### 核心指标

| 指标 | 公式/定义 | 解读 |
|------|-----------|------|
| **净利润** | 总利润 - 总亏损 - 总成本 | 最直接的收益衡量 |
| **夏普比率** | (策略收益 - 无风险利率) / 收益标准差 | >1较好，>2优秀。衡量承担单位风险获得的超额收益 |
| **索提诺比率** | (策略收益 - 无风险利率) / 下行标准差 | 只考虑下行波动，比夏普比率更严格 |
| **最大回撤** | max[(峰值 - 谷底) / 峰值] | 越小越好。历史最大亏损比例 |
| **卡玛比率** | 年化收益 / 最大回撤 | 收益风险比的另一种衡量 |
| **胜率** | 盈利次数 / 总交易次数 | 单独使用意义不大，需结合盈亏比 |
| **盈亏比** | 平均盈利 / 平均亏损 | 胜率低但盈亏比高，也可能是好策略 |
| **Alpha** | 策略超额收益（扣除基准收益后） | 衡量主动管理能力 |
| **Beta** | 策略对市场涨跌的敏感度 | Beta=1表示与市场同步 |

#### 好的回测报告应该包含

1. **收益曲线图** — 策略净值 vs 基准净值
2. **回撤图** — 展示每个时间点的回撤幅度
3. **月度收益表** — 各月收益分布（正收益月份占比）
4. **交易明细** — 每笔交易的买入/卖出时间、价格、盈亏
5. **敏感性分析** — 参数变化对策略表现的影响

### 5.4 回测中的常见陷阱

1. **未来函数** — 使用未来数据预测过去（如用今日收盘价计算今日信号）
2. **幸存者偏差** — 只回测当前存在的股票，忽略了已退市的股票
3. **前视偏差** — 使用当时无法获得的数据（如用调整后的财报数据回测）
4. **忽略交易成本** — 实盘交易成本会大幅降低收益
5. **数据窥探** — 反复在同一数据集上测试，直到找到「好结果」

---

## 六、风控体系

### 6.1 止损策略

**固定百分比止损**：当价格下跌到买入价的一定百分比时自动平仓。

```python
stop_loss_pct = 0.08  # 8%止损线

def next(self):
    if self.position:
        entry_price = self.position.price
        current_price = self.datas[0].close[0]
        if (entry_price - current_price) / entry_price > stop_loss_pct:
            self.close()
```

**技术指标止损**：基于ATR（平均真实波幅）设置动态止损点。

```python
import talib

# 计算14日ATR
atr = talib.ATR(high, low, close, timeperiod=14)

# 动态止损：买入价 - 2倍ATR
stop_price = entry_price - 2 * atr[0]
```

**时间止损**：持仓超过一定天数仍亏损，强制平仓。

### 6.2 仓位管理

**固定比例仓位**：每次交易投入总资金的固定比例（如2%）。

```python
def calculate_position_size(self, price, risk_pct=0.02):
    """根据固定风险比例计算仓位"""
    account_value = self.broker.getvalue()
    risk_amount = account_value * risk_pct
    # 假设止损幅度为8%
    stop_pct = 0.08
    position_size = risk_amount / (price * stop_pct)
    return int(position_size / 100) * 100  # A股需为100的整数倍
```

**凯利公式**（理论最优仓位）：

```
f* = (p × b - q) / b

其中：
f*：最优仓位比例
p：胜率
q：败率（1-p）
b：盈亏比（平均盈利/平均亏损）
```

**注意**：凯利公式在实际应用中容易过度激进，通常建议使用「分数凯利」（如半凯利）

**ATR仓位管理**：根据波动性动态调整仓位，波动大时仓位小，波动小时仓位大。

### 6.3 风险度量指标

| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **VaR** (在险价值) | 给定置信水平下，一定时间内的最大损失 | 历史模拟法 / 方差-协方差法 / 蒙特卡洛模拟 |
| **CVaR** (条件在险价值) | VaR超越时，平均损失是多少 | VaR的尾部条件期望 |
| **最大回撤** | 从峰值到谷底的最大跌幅 | max[(峰值-谷底)/峰值] |
| **年化波动率** | 收益率标准差的年化 | 日波动率 × √252 |

### 6.4 投资组合层面的风控

1. **分散化** — 不把所有资金放在一个策略/一个品种上
2. **相关性控制** — 组合内策略/品种的相关性不宜过高
3. **最大持仓限制** — 单只股票/单个行业不超过组合一定比例
4. **杠杆控制** — 严格控制杠杆倍数，避免强制平仓风险

---

## 七、完整实战案例：双均线策略回测

以下是一个完整的双均线策略回测流程（以贵州茅台为例）：

### 步骤1：获取数据

```python
import tushare as ts
import pandas as pd
import backtrader as bt

# 初始化Tushare
ts.set_token('YOUR_TOKEN')
pro = ts.pro_api()

# 获取数据
df = pro.daily(ts_code='600519.SH', start_date='20230101', end_date='20231231')
df = df.sort_values('trade_date')
df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
df.set_index('trade_date', inplace=True)
```

### 步骤2：计算均线并生成信号

```python
# 计算均线
df['ma5'] = df['close'].rolling(window=5).mean()
df['ma20'] = df['close'].rolling(window=20).mean()

# 生成信号：1=金叉（买入），-1=死叉（卖出）
df['signal'] = 0
df.loc[(df['ma5'].shift(1) <= df['ma20'].shift(1)) & (df['ma5'] > df['ma20']), 'signal'] = 1
df.loc[(df['ma5'].shift(1) >= df['ma20'].shift(1)) & (df['ma5'] < df['ma20']), 'signal'] = -1
```

### 步骤3：用Backtrader回测

```python
# 将数据转换为Backtrader格式
from backtrader.feeds import PandasData

class MyData(PandasData):
    params = (
        ('datetime', None),  # 使用index
        ('open', 'open'),
        ('high', 'high'),
        ('low', 'low'),
        ('close', 'close'),
        ('volume', 'vol'),
    )

data_feed = MyData(dataname=df)

# 创建Cerebro
cerebro = bt.Cerebro()
cerebro.adddata(data_feed)
cerebro.addstrategy(SmaCross)
cerebro.broker.setcash(100000.0)
cerebro.broker.setcommission(commission=0.001)  # 千分之一手续费

# 添加分析指标
cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')

# 运行
results = cerebro.run()
strat = results[0]

# 输出结果
print(f"夏普比率: {strat.analyzers.sharpe.get_analysis()}")
print(f"最大回撤: {strat.analyzers.drawdown.get_analysis()}")
```

---

## 八、策略开发检查清单

在开发一个量化策略时，依次检查以下事项：

### 逻辑层面
- [ ] 策略逻辑是否有经济学/行为学解释？（避免纯粹数据挖掘）
- [ ] 策略赚的什么钱？（趋势、反转、套利、风险溢价？）
- [ ] 策略在哪种市场环境下有效？哪种环境下会失效？

### 数据层面
- [ ] 数据来源是否可靠？
- [ ] 是否处理了复权（分红送股）？
- [ ] 是否处理了停牌、退市股票？（避免幸存者偏差）
- [ ] 数据频率是否合适？（日频/分钟频/ Tick频）

### 回测层面
- [ ] 是否设置了合理的手续费和滑点？
- [ ] 是否做了样本外检验？
- [ ] 参数是否过度拟合？
- [ ] 策略在不同时间段（牛市/熊市/震荡市）表现是否稳定？
- [ ] 是否存在未来函数？

### 风险层面
- [ ] 最大回撤是否在可接受范围内？
- [ ] 是否有止损机制？
- [ ] 仓位管理是否合理？
- [ ] 极端行情下（如闪崩）策略表现如何？

### 实盘层面
- [ ] 策略的资金容量是多少？（高频策略容量通常较小）
- [ ] 交易成本对收益的影响有多大？
- [ ] 实盘执行时是否存在流动性问题？

---

## 九、诚实边界（Limitations）

量化交易不是「印钞机」。以下局限必须明确告知用户：

### 9.1 过拟合风险

> **这是量化交易最大的陷阱**。

回测结果越好，越要保持怀疑。一个在历史数据上表现完美的策略，实盘很可能惨不忍睹。原因：
- 金融数据信噪比极低
- 参数优化本质上是在拟合噪音
- 「漂亮」的回测曲线往往是过拟合的产物

### 9.2 滑点与交易成本

回测中设置的手续费（如0.1%）往往低估了真实成本：
- **滑点**：大单交易时，实际成交价可能明显偏离下单价
- **市场冲击**：大单会改变市场价格，导致后续成交价更差
- **机会成本**：想买的时候买不到（涨停），想卖的时候卖不出（跌停）

### 9.3 流动性风险

- 小市值股票/山寨币的流动性极差，大资金无法顺利进出
- 回测中的「完美成交」假设在实盘中往往不成立
- 加密市场大多数山寨币的流动性是假象（刷量）

### 9.4 黑天鹅事件

- 量化模型基于历史数据，无法预测从未发生过的极端事件
- 2008年金融危机、2020年3月新冠疫情暴跌、2022年LUNA归零
- 这些事件中，很多量化策略同时失效（相关性趋同）

### 9.5 因子衰减

- 一个因子被发现并广泛使用后，Alpha会迅速衰减
- 价值因子在2010年代后期表现不佳，部分原因是太多人使用
- 加密市场的Alpha衰减更快（信息传播极快）

### 9.6 监管与结构变化

- 监管变化可能消除某些结构性机会（如某些套利策略）
- 市场结构变化（如被动投资占比从10%上升到50%）可能改变因子表现
- 交易规则变化（如T+0试点、涨跌幅限制调整）会影响策略有效性

---

## 十、学习路径推荐

### 初学者（0-3个月）

1. **掌握Python基础** — 重点学习pandas数据处理
2. **理解量化交易基本概念** — 收益率、波动率、夏普比率、最大回撤
3. **实现第一个策略** — 双均线策略（金叉买入、死叉卖出）
4. **学会用Backtrader回测** — 了解回测框架的基本使用

**推荐资源**：
- QuantConnect教程库（GitHub: gh_mirrors/tutorials2/Tutorials）
- CSDN博客：「从零开始量化交易：Python金融编程实战指南」

### 进阶者（3-12个月）

1. **学习多种经典策略** — 动量、均值回归、配对交易、多因子选股
2. **深入理解因子模型** — Fama-French三因子/五因子模型
3. **掌握风险管理** — 止损、仓位管理、VaR计算
4. **学会识别过拟合** — 样本外检验、交叉验证

**推荐资源**：
- 博客园：「量化交易系列（四）：多因子模型——华尔街印钱机器的核心秘密」
- CSDN：「量化交易入门：经典策略大盘点与Python实战」

### 专业者（12个月+）

1. **研究机器学习在量化中的应用** — LSTM、随机森林、XGBoost
2. **学习高频交易技术** — 低延迟系统、订单簿分析
3. **开发实盘交易系统** — 对接券商API、实时监控、自动下单
4. **持续迭代策略** — 跟踪学术前沿、挖掘新的有效因子

---

## 十一、常用代码模板

### 模板1：获取A股数据（Tushare）

```python
import tushare as ts
import pandas as pd

ts.set_token('YOUR_TOKEN')
pro = ts.pro_api()

def get_stock_data(ts_code, start, end):
    """获取股票日线数据"""
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    df = df.sort_values('trade_date')
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df.set_index('trade_date', inplace=True)
    return df

# 使用示例
df = get_stock_data('600519.SH', '20230101', '20231231')
print(df[['open', 'high', 'low', 'close', 'vol']].head())
```

### 模板2：计算常用技术指标

```python
import talib as ta

# 计算MACD
df['macd'], df['macd_signal'], df['macd_hist'] = ta.MACD(df['close'], fastperiod=12, slowperiod=26, signalperiod=9)

# 计算RSI
df['rsi'] = ta.RSI(df['close'], timeperiod=14)

# 计算布林带
df['bb_upper'], df['bb_middle'], df['bb_lower'] = ta.BBANDS(df['close'], timeperiod=20)

# 计算ATR
df['atr'] = ta.ATR(df['high'], df['low'], df['close'], timeperiod=14)
```

### 模板3：Backtrader策略骨架

```python
import backtrader as bt

class MyStrategy(bt.Strategy):
    params = (
        ('param1', 10),
        ('param2', 20),
    )
    
    def log(self, txt, dt=None):
        """日志记录"""
        dt = dt or self.datas[0].datetime.date(0)
        print(f'{dt.isoformat()}, {txt}')
    
    def __init__(self):
        """初始化策略"""
        self.dataclose = self.datas[0].close
        self.order = None  # 待处理订单
        
    def notify_order(self, order):
        """订单状态通知"""
        if order.status in [order.Submitted, order.Accepted]:
            return  # 订单已提交/接受，等待进一步通知
        
        if order.status == order.Completed:
            if order.isbuy():
                self.log(f'买入执行, 价格: {order.executed.price:.2f}, 成本: {order.executed.value:.2f}, 手续费: {order.executed.comm:.2f}')
            elif order.issell():
                self.log(f'卖出执行, 价格: {order.executed.price:.2f}, 收入: {order.executed.value:.2f}, 手续费: {order.executed.comm:.2f}')
        
        self.order = None  # 订单完成，重置
    
    def next(self):
        """每个bar执行一次"""
        if self.order:  # 有待处理订单，不再下单
            return
        
        if not self.position:  # 无持仓
            # 买入条件
            if self.dataclose[0] > self.dataclose[-1]:  # 简单示例：今天收盘价比昨天高就买
                self.log(f'创建买入订单, 价格: {self.dataclose[0]:.2f}')
                self.order = self.buy()
        
        else:  # 有持仓
            # 卖出条件
            if self.dataclose[0] < self.dataclose[-1]:
                self.log(f'创建卖出订单, 价格: {self.dataclose[0]:.2f}')
                self.order = self.close()
```

---

## 附录：推荐资源

### 书籍
- 《量化投资：以Python为工具》— 蔡立耑
- 《打开量化投资的黑箱》— Rishi K Narang
- 《主动投资组合管理》— Grinold & Kahn

### 在线资源
- **QuantConnect**：专业量化交易平台（Lean引擎，支持Python/C#）
- **BigQuant**：中文量化平台，提供免费回测环境
- **聚宽（JoinQuant）**：国内量化平台，有免费版

### 数据接口
- **Tushare**：https://tushare.pro （需注册）
- **AkShare**：https://github.com/akfamily/akshare （免费开源）
- **Baostock**：http://baostock.com （免费，无需注册）

### 开源框架
- **Backtrader**：https://github.com/mementum/backtrader
- **Zipline**：https://github.com/quantopian/zipline （已停止维护）
- **quantstats**：https://github.com/ranaroussi/quantstats
- **QLib**（微软）：https://github.com/microsoft/QLib （AI量化投资平台）

---

## 信息截止时间

本Skill的知识截止时间为 **2026年6月**。量化交易领域发展迅速，新的研究成果和市场变化持续涌现。建议用户：

1. 关注学术前沿（NBER、SSRN上的量化金融论文）
2. 跟踪业界实践（AQR、Two Sigma等头部量化机构的研究报告）
3. 持续回测和验证策略，不要盲目相信历史表现

---

*本Skill由女娲（思维蒸馏师）蒸馏自公开量化交易资料，涵盖CSDN、博客园、雪球、搜狐等平台的权威内容。如有错误，欢迎指正。*
