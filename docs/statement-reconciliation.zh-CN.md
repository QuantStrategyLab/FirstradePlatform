# 对账：费用拆分与估值口径

有两类对账差异看起来无法解释，但原因固定且已记录。
`application/statement_reconciliation.py` 用纯离线函数对它们建模，不调用券商、不读取凭据、不写入数据。

## 人工委托卖单：经纪人协助费 + 监管费

描述中含 `BROKER/DEALER ORDER`（包括 `MARGIN SELLOUT`）的卖单，到账金额小于 `数量 x 价格`：

```
预期净额 = 数量 x 价格 - 经纪人协助费 - 监管费
```

- 经纪人协助费：每笔 `$19.95`（Firstrade 费率页面）。
- 监管费：SEC Section 31（`每百万美元费率 x 成交额`）加 FINRA TAF（每股费率，单笔设上限）。未取整金额相加后向上取整到分，只要产生费用最低 `$0.01`。
- 拆分后残差在 `+/- $0.01` 内记为 `explained`；更大为 `unexplained`；交易日期超出费率表范围记为 `unsupported`（失败即关闭，不猜测）。

示例，2026-08-28 IBIT：`2 x 43.7724 = 87.5448`，减去 `19.95` 和 `0.01` 得 `67.58`，与实际到账一致。此前的“无法解释 `19.96`”即这两项费用之和。

费率以带生效日期的分段形式放在 `application/firstrade_fee_schedule.py`（SEC 自 2025-05-14 起 `0.00`，自 2026-04-04 起每百万 `20.60`；TAF 2026 年每股 `0.000195`，2026-10-01 至 2026-12-31 暂停）。SEC 或 FINRA 发布新费率时更新分段；`load_fee_schedule()` 会校验顺序和取值。

## 估值口径差异

余额接口与持仓接口对同一批股票使用不同报价口径，因此在数量和现金一致时，`long_stock_value`（余额接口）与持仓 `market_value` 之和可能相差几分钱。`valuation_basis_difference` 把它单独报告：

| 状态 | 含义 |
| --- | --- |
| `match` | 完全相等。 |
| `valuation_basis_difference` | 非零，且在 `max($0.10, 市值的 0.1%)` 之内。 |
| `unexplained` | 超过容差。 |
| `unavailable` | 缺少输入。 |

示例，2026-10-31：余额 `130.56` 对持仓 `130.6232`，差 `0.0632`，在 `0.1306` 容差内。

只读采集器现在把 `long_stock_value` 作为独立的余额事实保留（`balances.long_stock_value`，覆盖标记 `long_stock_value_available`）。`build_firstrade_account_snapshot(..., include_valuation_basis=True)` 可把它与 `net_assets` 一并持久化。该选项默认关闭：QRS 控制台目前只接受 Firstrade 余额行中的 `currency` 和 `net_assets`，会拒绝额外字段；启用前需先扩展该 schema。
