# EP-07：可追溯成本与毛利场景

状态：Accepted for executable PoC
上游：EP-01、EP-03、EP-06、M01
下游：EP-08、EP-09、EP-10

## 1. 职责与边界

EP-07 为每一份已确认供应商报价、RFQ 行和目标数量建立独立成本场景，组合供应商产品价、包装、物流、保险、税费、支付费用、质检、损耗和风险准备金，并确定性计算单位落地成本、最低可接受价格和贡献毛利。

EP-07 不选择或排序供应商，不生成客户报价，不抓取实时费率，不发送消息，不下单或付款。多个供应商只会得到多份并列场景；输出中不得出现 winner、selected supplier 或采购授权。

## 2. 强制规则

- 所有金额、比例、数量和算术使用 `Decimal`；拒绝 float 和 bool。
- 产品成本只能来自 repository-saved 的精确 EP-06 quote snapshot 与 normalization run。
- 补充成本必须有明确币种、数量/分摊基准、有效时点、证据及人工决定。
- unknown、missing、excluded、无金额的 separately quoted 和未核实假设绝不能转换为 0。
- 跨币种转换必须绑定精确 FX snapshot；未来、过期或方向不匹配的 FX 必须阻断。
- per-order/per-shipment 成本只有在目标数量明确且单位兼容时才能分摊。
- 关税、支付费、损耗和风险准备金只允许版本化 policy 中有限的 Decimal 公式；不接受任意表达式。
- 历史快照不可覆盖；输入变化必须生成显式 superseding version。

## 3. 首版对象和服务

- `CostScenarioPolicyVersion`
- `CostComponentKind`
- `CostInputState`
- `CostInput`
- `CostAssumptionDecision`
- `ResolvedCostComponent`
- `CostScenarioSnapshot`
- `CostScenarioService`
- `CostScenarioRepositoryPort`
- `CostingSourceReader`

## 4. 计算合同

- `landed_cost_per_unit = Σ 所有适用且已解析的经常性单位成本`。
- 任一 policy-required 成本仍 unresolved 时，`landed_cost_per_unit` 和价格结论必须为 `None`。
- `minimum_sale_price = fixed_cost / (1 - target_margin_rate - sale_based_fee_rate)`；分母不大于 0 时阻断。
- `contribution_margin = proposed_sale_price - landed_cost_per_unit - sale_based_fee`。
- `contribution_margin_rate = contribution_margin / proposed_sale_price`，销售价不大于 0 时不计算。
- 内部 calculation value 不舍入；仅可按 policy 生成独立 display value。

## 5. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP07-AC-001 | P0 | Decimal-only；float、无币种、无数量基准和无 FX 的跨币种输入均拒绝 |
| EP07-AC-002 | P0 | 产品成本精确追溯到已保存 EP-06 quote、normalization、RFQ/spec 和证据 |
| EP07-AC-003 | P0 | required 成本未知时 landed cost、最低价格和毛利均不生成，未知不等于 0 |
| EP07-AC-004 | P0 | 有金额的 separately quoted 独立计入；无金额时保持 unresolved |
| EP07-AC-005 | P0 | per-shipment 成本只在数量和单位明确时分摊 |
| EP07-AC-006 | P0 | FX、quote、normalization、decision 或 policy 晚于 as_of 时阻断 |
| EP07-AC-007 | P0 | 每项补充成本均需匹配的 append-only 人工决定和证据 |
| EP07-AC-008 | P0 | policy 指纹重算一致；同输入幂等，变化必须 supersede 当前 lineage tip |
| EP07-AC-009 | P0 | 最低价格和毛利按有限公式确定性重放，显示舍入不改变 calculation value |
| EP07-AC-010 | P0 | 多供应商只产生并列场景，输出无 winner、selection、approval、send、PO 或 payment 字段 |
| EP07-AC-011 | P0 | Ruff、strict mypy、模块测试及 EP-06 回归通过 |
