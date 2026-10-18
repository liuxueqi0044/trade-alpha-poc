# 外贸执行平面 EP-07～EP-10 详细架构

版本：1.0.0
状态：Verified，可执行 PoC 已通过独立验收
日期：2026-08-27

## 1. 本阶段交付结果

EP-01～EP-06 已经把匿名买家 RFQ 转换为冻结规格、合格供应商、零外发供应商询价包、已确认报价、归一化结果和可比性矩阵。EP-07～EP-10 继续完成公司设立前的内部 dry-run：

```text
EP-06 已确认报价与可比性
→ EP-07 每供应商成本/毛利场景
→ EP-08 商业、安全与交易风险 finding
→ EP-09 运营准备度与四眼审批
→ EP-10 客户报价包、沟通草稿和全链验证
```

终点是一份 `READY_FOR_HUMAN_APPROVAL` 或带明确原因的 `BLOCKED` 本地客户报价包。它不是已发送报价、合同、订单、付款或出口授权。

## 2. 全局不变量

1. 所有金额、比例和数量计算只使用 `Decimal`，拒绝 float/bool。
2. unknown、missing、stale、excluded、quarantined 和 separately quoted without amount 不得转换为 0 或 pass。
3. 每个下游服务从 repository/reader 读取精确 frozen snapshot，验证类型、ID、scope、内容指纹和 point-in-time。
4. 任一输入的业务时间晚于下游 `as_of` 时 fail-closed；未来决定不能进入历史视图。
5. 原始证据、人工决定、快照、审批和审计均 append-only；更正通过 supersedes 形成新 lineage。
6. 相同输入、policy 和 `as_of` 重跑必须得到相同 ID/指纹；幂等键相同但 payload 不同必须拒绝。
7. AI/解析器可以提出候选和草稿，不能确认金额、解决风险、批准或执行动作。
8. 系统不自动选择供应商；任何成本场景选择都必须由人类显式决定并记录理由。
9. M10 Admin 默认不等于业务 Approver；maker、reviewer、approver 必须分离。
10. 本阶段没有真实 outbound、订单、付款、签约、报关、邮箱或 CRM 写端口。

## 3. 复用决策

| 能力 | 复用 | 说明 |
|---|---|---|
| immutable typed contracts | Pydantic v2 | frozen + extra=forbid；不新增第二套 schema 框架 |
| 金额/币种/FX | EP-01 | MonetaryAmount、FxSnapshot、require_decimal |
| 单位转换 | Pint adapter | 只通过端口；持久化值不保存 Pint 对象 |
| 稳定内容指纹 | EP-01 stable_fingerprint | 所有 policy 与 snapshot 均重算验证 |
| 报价事实与归一化 | EP-06 | 不复制或改写 supplier quote 语义 |
| RBAC | M10 PolicyEngine/Identity | SOL 增加明确 execution review/approval actions |
| 审计 | M10 AppendOnlyAuditLog | 复用 hash-chain，不另建第二套审计算法 |
| 模板 | Jinja2 SandboxedEnvironment | StrictUndefined、BaseLoader、allow-list view |
| 安全输出检查 | M10 SecretScanner | 不记录或回显密钥/Restricted 内容 |

本阶段不新增 ERP、CRM、OPA、Temporal、Celery、邮件 SDK、支付 SDK 或任意公式引擎。有限规则用纯 Python/Decimal 更清晰、更容易审计。

## 4. 依赖与目录

```text
execution/contracts
        │
supplier_quotes (EP06)
        │
        ▼
costing (EP07)
        │
        ▼
commercial_risk (EP08)
        │
        ▼
operational_readiness (EP09) ── M10 RBAC/audit
        │
        ▼
customer_quotes (EP10) ─────── Jinja sandbox/local artifacts
```

允许依赖方向只向下游；EP-07～EP-10 不能反向修改报价、规格、供应商资格或证据。跨模块依赖必须经过只读端口。

## 5. EP-07：可追溯成本与毛利场景

### 5.1 权威输入

- repository-saved `SupplierQuoteSnapshot`；
- 精确 `QuoteNormalizationRun` 和一条 `NormalizedQuoteLine`；
- `SupplierComparisonMatrix` 及 relevant issues；
- 目标 RFQ line 与数量 tier；
- 版本化 `CostScenarioPolicyVersion`；
- 补充成本的证据、人工决定与必要 FX snapshot。

一份场景只绑定一个 supplier quote snapshot、一个 RFQ line 和一个数量 tier。多供应商比较只是若干场景的并列集合，不产生 ranking 或 winner。

### 5.2 成本状态

```text
known                 有金额、币种、基准和证据
unknown               已知此边界存在，但金额未知
not_applicable        policy 明确且有理由不适用
included_upstream     已包含在经确认上游价格中，防止重复计价
excluded_upstream     上游明确排除，不能自动当 0；若 policy required 则 unresolved
```

`known` 必须包含金额、quantity/allocation basis 和 provenance。其他状态不得夹带 amount。

### 5.3 成本分类

- supplier product；
- packaging；
- domestic freight；
- export clearance；
- international freight；
- insurance；
- import duty/tax；
- inspection/QC；
- payment fee；
- wastage；
- risk reserve；
- tooling/NRE 和 sample 作为非经常项单列。

EP-06 `ComponentType` 表示“供应商报价事实边界”，EP-07 `CostComponentKind` 表示“内部成本结论边界”，二者通过显式映射转换，不能复用同一枚举制造语义混淆。

### 5.4 有限计算

```text
landed_cost_per_unit
  = supplier_product
  + allocated recurring known costs
  + policy-derived wastage/risk reserve

minimum_sale_price
  = fixed_cost / (1 - target_margin_rate - sale_based_fee_rate)

contribution_margin
  = proposed_sale_price - landed_cost_per_unit - sale_based_fee
```

任一 required component unresolved、数量不可分摊、FX 无效或分母不正时，对应数值输出为 `None` 并附 blocking reason。内部 calculation value 不量化；display value 单独舍入。

### 5.5 安全边界

- 补充金额必须匹配 append-only `CostAssumptionDecision` 的 typed value；
- 决定的 evidence、scope、actor 和 `decided_at` 必须匹配场景；
- policy 比例是版本化固定 Decimal，不接受公式字符串或调用方临时函数；
- scenario JSON 不允许 supplier selection、approval、send、PO/payment 字段。

## 6. EP-08：商业、安全与交易风险策略

### 6.1 输出语义

EP-08 输出 `TradeRiskAssessmentSnapshot`，其中每个 `RiskFinding` 包含：

- stable code；
- dimension：safety/supplier/quote/cost/transaction/governance；
- severity：hard_block/needs_evidence/approval_required/warning；
- precise input refs/fingerprints；
- provenance refs；
- suggested action；
- 当前 resolved 状态及有效人工决定（如 policy 允许）。

最终 disposition：

```text
blocked
incomplete
approval_required
cleared_for_internal_dry_run
```

最后一个状态只允许继续内部演练，不包含外发或订单授权。

### 6.2 deny-by-default 规则

- spec 未冻结、安全用途未知或 hard exclusion → hard block/needs evidence；
- supplier qualification 过期/阻断、screening stale/error/candidate → 不得 clear；
- quote 过期、quarantine、scope mismatch、Incoterm/named place/MOQ/validity 异常 → finding；
- EP-07 required component unresolved、FX 缺失、margin 无法计算或低于 floor → block/approval；
- DDP、账期、税务、路线、收付款身份或专业许可缺证据 → specialist review，不能自动宣称合规；
- M10 license/authorization deny → hard block。

普通人工决定只能逐项处理 policy 标注为可接受的 approval/warning，不能覆盖 hard block 或伪造缺失证据。新证据必须生成新 assessment。

## 7. EP-09：运营准备度与四眼审批

### 7.1 准备状态不是脑内 TODO

`OperationalReadinessSnapshot` 保存以下 item 的状态、证据、验证人和时间：

```text
legal_entity_registered
registered_address_verified
corporate_domain_ready
corporate_email_ready
bank_account_ready
payment_route_ready
customs_registration_ready
export_operator_ready
supplier_verified
sample_process_ready
qc_process_ready
freight_forwarder_ready
```

每项为 verified/pending/blocked/not_applicable。verified 无证据或未来 verified_at 无效；not_applicable 必须由 policy 允许并带理由。

### 7.2 capability 分层

```text
research_only
internal_quote_dry_run
outreach
customer_quote
order
export
```

当前 policy 的 `production_actions_enabled=false`。因此只可评估并批准 `internal_quote_dry_run`；其他 capability 即使所有 item 为 verified 也硬阻断，防止测试数据意外开启生产动作。

### 7.3 generic approval resource

`ApprovalRequest` 不直接依赖 EP-10 模型，只绑定：

- resource_type/resource_id/resource_fingerprint；
- EP-07 cost scenario ref/fingerprint；
- EP-08 risk assessment ref/fingerprint；
- readiness evaluation ref/fingerprint；
- maker identity；
- policy/version/as_of；
- required stages 与 idempotency key。

审批顺序：maker submit → reviewer review → independent approver approve。三人不得相同；M10 Admin 没有隐含审批权。决定早于 request、晚于 expiry、引用旧 fingerprint 或不是当前 stage 时拒绝。

同一 request/stage 只有一个 effective terminal decision。重复相同 payload 幂等；CAS/并发冲突和 idempotency key payload 冲突 fail-closed。

## 8. EP-10：客户报价包与沟通草稿

### 8.1 人工商业选择

EP-10 不从成本数组中取最低值。人类必须提交 `CustomerCommercialDecision`，显式选择：

- exact EP-07 cost scenario；
- 客户价格、币种与 quantity basis；
- Incoterm/rules/named place；
- 有效期；
- 非空付款、交付、假设、排除和披露；
- actor、角色、理由、时间和受控 provenance。

每个非空字段逐项匹配决定。更改任何字段必须 supersede 当前决定 tip。

### 8.2 package 状态派生

调用方不能指定状态：

- INCOMPLETE：关键事实、成本、决定或证据缺失；
- BUDGETARY_ONLY：policy 允许且所有不确定性已明确披露；
- BLOCKED：EP-08/EP-09/policy hard block；
- APPROVAL_REQUIRED：内容完整，但精确 basis 资源尚未完成内部四眼批准；
- READY_FOR_HUMAN_APPROVAL：内部前置门全部满足，可供最终人类审核。

READY 仍不表示已批准外发。

### 8.3 客户 view 与模板

Renderer 只接收 allow-list view：客户别名、已确认产品说明、数量、客户价格/条款、有效期和强制披露。不得传入 package 对象或以下字段：

- supplier identity/profile/RFQ/quote refs；
- supplier cost、minimum price、margin、risk reserve；
- internal findings、approval actors、audit details；
- artifact 路径、raw evidence、Restricted data。

`SandboxedEnvironment + StrictUndefined + BaseLoader` 禁止文件、include/import、dunder traversal 和方法执行。用户/供应商输入只作为值插值，不作为模板源码。

### 8.4 结构性零外发

EP-10 不定义真实 outbound port，不保存真实 recipient address、credential 或 delivery token。生成的 `CustomerCommunicationDraft` 只有受控 recipient reference、渠道意图、内容 hash、本地 artifact ref 和 `rendered_not_sent` disposition。

代码和状态枚举中不得有 sent/delivered/accepted/order/payment 成功路径。未来真实外发必须成为独立模块、独立 policy 和独立架构评审。

### 8.5 全链 manifest

`ExecutionChainManifest` 串联：

```text
RFQ evidence/receipt
→ ProductSpecVersion
→ SupplierQualificationSnapshot
→ SupplierRfqPackageSnapshot
→ SupplierQuoteIntakeReceipt/SupplierQuoteSnapshot
→ QuoteNormalizationRun/SupplierComparisonMatrix
→ CostScenarioSnapshot
→ TradeRiskAssessmentSnapshot
→ ReadinessEvaluation/ApprovalRequest
→ CustomerCommercialDecision/CustomerQuotePackageSnapshot
→ M10 AuditEvent hashes
```

Verifier 从可信 package repository 重载 EP-10，再检查其绑定的 EP-03、EP-05、EP-07、EP-08、EP-09 exact IDs/fingerprints，以及 scope、supersedes tips、时间单调性和 M10 hash chain。`BUDGETARY_ONLY` 没有审批请求时，EP-09 节点必须使用由 package fingerprint 确定性派生的 `not_required_for_budgetary_only` sentinel，不能伪装成已批准。它只返回 verified/invalid 与 reason codes，不修复链条。

## 9. 跨模块黄金场景

1. 复用 EP-04～EP-06 三供应商夹具与已冻结规格。
2. 为三份报价分别生成成本场景；补充物流、保险等金额全部来自固定证据和人工决定。
3. EP-08 对每个场景生成 findings；至少一份完整场景可继续内部 dry-run，阻断场景保留原因。
4. 当前现实准备快照保持公司/银行/出口项 pending；internal dry-run capability 可继续，真实 customer quote/order/export 全部 blocked。
5. maker 提交精确成本/风险 basis，reviewer 与 approver 分别完成两阶段内部批准。
6. 人类显式选择一份场景并确认客户价格和条款，EP-10 生成本地报价与沟通草稿。
7. package 为 READY_FOR_HUMAN_APPROVAL 或有明确 BLOCKED 原因；任何输出都没有真实外发能力。
8. execution chain manifest 与 M10 audit chain 验证成功。

反例必须覆盖：未来 FX/quote/decision、成本 unknown 被当 0、低毛利、hard exclusion、duck object、伪造 fingerprint、同人审批、Admin-only 审批、旧批准移植、模板内省、供应商身份泄漏、试图外发和 audit tampering。

## 10. 开发包与并行边界

### EP-07（Terra）

1. typed models/policy fingerprint；
2. source readers 和 append-only repository；
3. supplemental decision 与 PIT 校验；
4. Decimal pure calculator；
5. lineage/idempotency；
6. 独立与 EP-06 回归测试。

### EP-08（Luna）

1. risk policy/findings/assessment；
2. 精确上游 reader 与 fingerprint guard；
3. deny-by-default rule evaluator；
4. per-finding human decisions；
5. audit port；
6. 独立与 EP-03～EP-07 回归测试。

### EP-09/EP-10（Terra）

1. readiness items/policy/evaluator；
2. generic maker-checker workflow；
3. customer commercial decisions/package repository；
4. safe renderer 与 local draft；
5. chain manifest/verifier；
6. 独立模块测试。

### SOL

负责 M10 action 扩展、跨模块 readers/adapters、黄金场景、安全终审、文档/验收索引、全量验证和最终 GitHub 交付。

## 11. 完成定义

EP-07～EP-10 只有同时满足以下条件才算完成：

1. 四份 canonical spec 与实现一致；
2. 每个模块 P0 acceptance 有独立自动化测试；
3. 三供应商黄金路径能确定性产生 READY 或有原因 BLOCKED package；
4. 金额、证据、风险、准备度、审批和草稿可沿 manifest 下钻；
5. 未知值不变成 0、pass 或外部事实；
6. 任何 hard block、时间穿越、指纹伪造和审批绕过都被阻断；
7. 全程 0 外发、0 下单、0 付款、0 商业承诺；
8. EP-01～EP-10、M01～M11 全量测试、Ruff、strict mypy 和依赖检查通过；
9. 独立只读终审无剩余 P0/P1；
10. 变更提交并推送到配置的 GitHub remote。
