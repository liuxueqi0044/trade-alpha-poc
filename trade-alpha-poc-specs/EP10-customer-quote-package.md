# EP-10：客户报价包、沟通草稿与全链审计

状态：Accepted for executable PoC
上游：EP-01～EP-09、M01、M10
下游：未来真实外发适配器（不在本阶段）

## 1. 职责与边界

EP-10 将已通过内部成本/风险/readiness 审批的精确场景和人工确认的客户商业条款，冻结为人类可审核的 `CustomerQuotePackageSnapshot`，生成本地客户报价草稿、沟通草稿和可验证的执行链清单。

本模块没有 SMTP、即时通信、CRM、门户、上传、签约、订单或付款端口。成功终点是本地草稿和 `READY_FOR_HUMAN_APPROVAL`，不是 sent、delivered、accepted 或 ordered。

## 2. 强制规则

- 客户价格、币种、数量、Incoterm、有效期、付款、交付及每项事实均需匹配 append-only 人工决定。
- 人工选择成本场景必须显式记录；不得由最低成本、排序或模型自动选择供应商。
- package 精确绑定 EP-09 已批准 resource fingerprint、EP-07 成本、EP-08 风险、RFQ/spec 和模板/披露 policy。
- 成本、毛利、供应商身份、供应商原始报价、审批内部信息和 Restricted 数据不得进入客户 view。
- 渲染使用 `SandboxedEnvironment + StrictUndefined + BaseLoader`，只接受 allow-list view；上传文本永不作为模板执行。
- 任何输入或模板变化产生新 package version 和新审批；terminal package 不可改写。
- 全链清单必须验证 EP-01～EP-10 每一条 ID、指纹和时间边；缺失、错配、未来引用或审计篡改均失败。

## 3. 首版对象和服务

- `CustomerQuoteStatus`：`INCOMPLETE`、`BUDGETARY_ONLY`、`BLOCKED`、`APPROVAL_REQUIRED`、`READY_FOR_HUMAN_APPROVAL`
- `CustomerQuotePolicyVersion`
- `CustomerCommercialDecision`
- `CustomerQuoteLine`
- `CustomerQuoteTerms`
- `CustomerQuotePackageSnapshot`
- `CustomerCommunicationDraft`
- `ExecutionChainManifest`
- `ChainVerificationResult`
- `CustomerQuoteService`
- `ExecutionChainVerifier`
- `CustomerQuoteRepositoryPort`
- `CustomerQuoteRendererPort`

## 4. 状态语义

- `INCOMPLETE`：缺关键规格、客户条款、成本或证据。
- `BUDGETARY_ONLY`：policy 明确允许、且所有不确定性在客户披露中可见的非承诺预算草稿。
- `BLOCKED`：安全、风险、运营准备或 policy hard block。
- `APPROVAL_REQUIRED`：内容完整但精确 package 尚未完成所需内部审批。
- `READY_FOR_HUMAN_APPROVAL`：全部内部前置门已满足，可供最终人类审核；仍没有外发授权。

## 5. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP10-AC-001 | P0 | 客户价格和所有非空商业条款逐项匹配有效人工决定 |
| EP10-AC-002 | P0 | 人工成本场景选择绑定精确 EP-07 snapshot，系统不自动选择供应商 |
| EP10-AC-003 | P0 | package 只接受 repository-saved、未过期且 fingerprint 一致的 EP-08/EP-09 输入 |
| EP10-AC-004 | P0 | 状态按 incomplete/budgetary/blocked/approval-required/ready 规则确定，不能由调用方指定 |
| EP10-AC-005 | P0 | 客户 view 不包含供应商身份、成本、毛利、内部风险、原始证据路径或审批身份 |
| EP10-AC-006 | P0 | Jinja sandbox、StrictUndefined、字段白名单和必要披露 fail-closed |
| EP10-AC-007 | P0 | 模板/价格/条款/成本/风险/readiness 变化必须新版本且旧批准不继承 |
| EP10-AC-008 | P0 | 相同输入幂等；冲突幂等键与伪造 supersedes 均拒绝 |
| EP10-AC-009 | P0 | 全链 manifest 验证 ID、fingerprint、PIT 和 M10 audit hash-chain，篡改即失败 |
| EP10-AC-010 | P0 | 输出中无真实 recipient、credential、send/delivered/accepted/order/payment 成功路径 |
| EP10-AC-011 | P0 | 所有 artifact 仅本地生成；任何外发尝试在结构上不可达或被 exact Null adapter 阻断 |
| EP10-AC-012 | P0 | 三供应商 golden case 形成一份明确 READY 或有原因 BLOCKED 的客户报价包 |
| EP10-AC-013 | P0 | Ruff、strict mypy、模块测试、EP-01～EP-09 与跨模块回归通过 |
