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
- 数量和价格必须大于 0，否则该行为 `unsupported`（`quantity_not_positive` / `price_not_positive`）。
- CSV 的 `Date` 列是**交易日**而非结算日。2026-08-28 IBIT 一行中 `Date` 为 `8/28/2026`，描述含 `EXEC TIME: 2026-08-28 ...` 与 `S/D: 08/31/2026`（T+1）。SEC Section 31 与 FINRA TAF 以交易日计费，因此按 `Date` 选择费率，`settlement_date` 仅作展示；`Date` 与 `EXEC TIME` 不一致或不早于 `S/D` 时记为 `unsupported`（`date_column_not_trade_date`）。

示例，2026-08-28 IBIT：`2 x 43.7724 = 87.5448`，减去 `19.95` 和 `0.01` 得 `67.58`，与实际到账一致。此前的“无法解释 `19.96`”即这两项费用之和。

费率以带生效日期的分段形式放在 `application/firstrade_fee_schedule.py`（SEC 自 2025-05-14 起 `0.00`，自 2026-04-04 起每百万 `20.60`）。TAF（普通股/ETF）2026 年每股 `0.000195`（单笔上限 `$9.79`），2026-10-01 至 2026-12-31 暂停（SR-FINRA-2026-021）；因 SR-FINRA-2026-020 将剩余涨幅推迟两年，2027、2028 年仍为 `0.000195` / `$9.79`，2029-01-01 起 `0.000232` / `$11.61`，2030-01-01 起 `0.000240` / `$12.05`，2031-01-01 起 `0.000249` / `$12.50`。SEC 或 FINRA 发布新费率时更新分段；`load_fee_schedule()` 会校验顺序和取值。

`verified_through`（当前 `2026-12-01`）是费率最后一次对照公开来源核实的交易日。之后的交易日为 `unsupported`（`..._not_verified_after_<日期>`）：SEC 2027 财年 Section 31 费率尚未公布（FY2026 费率在 2027 财年拨款法案生效后 60 天内继续有效），上述 FINRA 备案虽即时生效但仍可能被 SEC 暂停。更新来源核实时需在同一变更中推进 `verified_through`。

## 估值口径差异

余额接口与持仓接口对同一批股票使用不同报价口径，因此在数量和现金一致时，`long_stock_value`（余额接口）与持仓 `market_value` 之和可能相差几分钱。`valuation_basis_difference` 把它单独报告：

| 状态 | 含义 |
| --- | --- |
| `match` | 完全相等。 |
| `valuation_basis_difference` | 非零，且在 `min(max($0.10, 市值的 0.1%), $25.00)` 之内。 |
| `unexplained` | 超过容差、持仓数量不一致，或持仓为空但余额股票价值非零。 |
| `unavailable` | 缺少输入或输入为负。 |

`$25.00` 绝对上限避免大账户的 0.1% 掩盖真实差异。传入 `expected_quantities`（例如上一份快照或账本）可交叉核对股数：只有数量一致时价格口径差异才可信，数量不一致一律 `unexplained`（`quantity_mismatch`）。`quantity_check` 取值 `matched`、`mismatch` 或 `not_performed`；持仓为空只与零余额股票价值匹配。

示例，2026-10-31：余额 `130.56` 对持仓 `130.6232`，差 `0.0632`，在 `0.1306` 容差内。

只读采集器现在把 `long_stock_value` 作为独立的余额事实保留（`balances.long_stock_value`，覆盖标记 `long_stock_value_available` 与 `long_stock_value_ambiguous`）。顶层与 `result` 中的值冲突时，该值被置空并标记为 ambiguous，其余采集结果（现金、权益、持仓）照常返回。`build_firstrade_account_snapshot(..., include_valuation_basis=True)` 可把它与 `net_assets` 一并持久化。该选项默认关闭：QRS 控制台目前只接受 Firstrade 余额行中的 `currency` 和 `net_assets`，会拒绝额外字段；启用前需先扩展该 schema。
