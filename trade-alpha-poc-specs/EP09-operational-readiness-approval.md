# EP-09：运营准备度与四眼审批

状态：Accepted for executable PoC
上游：EP-07、EP-08、M10
下游：EP-10

## 1. 职责与边界

EP-09 把公司的真实物理准备状态建模为不可变快照，按目标 capability 评估缺口，并为精确资源指纹提供 maker-checker 审批。它回答“当前内部演练或真实动作是否具备前置条件”，不执行动作。

当前 PoC 只允许 `INTERNAL_QUOTE_DRY_RUN`。客户外发、订单、付款和出口 capability 即使资料齐全也受 `production_actions_enabled=false` 硬门阻断。

## 2. 强制规则

- readiness item 使用 `verified/pending/blocked/not_applicable`，不是无来源布尔值；verified 必须带证据和验证时间。
- policy 明确每种 capability 的 required items；unknown 和缺项不得通过。
- readiness snapshot、evaluation、approval request 和 decision 均不可变且 point-in-time。
- approval request 精确绑定 resource ID、resource fingerprint、EP-07/EP-08 refs 和 readiness evaluation。
- maker、reviewer、approver 必须是不同身份；Admin 默认没有业务审批权。
- reviewer 与 approver 通过 M10 的不同 Action 授权；审批时间不得早于 request 或依赖快照。
- 输入、策略、模板或资源内容发生变化后旧批准立即失去作用，不能移植。
- 同一 stage 只能有一个 effective terminal decision；并发/CAS 冲突 fail-closed。

## 3. 首版对象和服务

- `OperationalCapability`
- `ReadinessItemState`
- `ReadinessItem`
- `OperationalReadinessPolicyVersion`
- `OperationalReadinessSnapshot`
- `ReadinessEvaluationSnapshot`
- `ApprovalStage`
- `ApprovalRequest`
- `ApprovalDecision`
- `OperationalReadinessService`
- `ApprovalWorkflowService`
- `OperationalReadinessRepositoryPort`
- `ExecutionAuthorizationPort`
- `ExecutionAuditPort`

## 4. 首版 readiness items

- `legal_entity_registered`
- `registered_address_verified`
- `corporate_domain_ready`
- `corporate_email_ready`
- `bank_account_ready`
- `payment_route_ready`
- `customs_registration_ready`
- `export_operator_ready`
- `supplier_verified`
- `sample_process_ready`
- `qc_process_ready`
- `freight_forwarder_ready`

## 5. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP09-AC-001 | P0 | verified readiness item 必须有证据和 UTC 验证时间；unknown 不得通过 |
| EP09-AC-002 | P0 | policy/config 指纹重算一致，未来或过期事实不能进入 as_of evaluation |
| EP09-AC-003 | P0 | internal dry-run 与 customer quote/order/export capability 分离 |
| EP09-AC-004 | P0 | production_actions_enabled=false 时所有真实动作硬阻断 |
| EP09-AC-005 | P0 | request 只接受 repository-saved EP-07/EP-08 和 readiness 输入及精确指纹 |
| EP09-AC-006 | P0 | maker/reviewer/approver 同人、角色错误、Admin-only 和时间倒序均拒绝 |
| EP09-AC-007 | P0 | reviewer 与 approver 两阶段均有效后才产生 internal approval |
| EP09-AC-008 | P0 | hard block/unresolved risk/成本不完整时不能提交或批准 |
| EP09-AC-009 | P0 | 资源指纹或依赖变化后旧批准不可复用 |
| EP09-AC-010 | P0 | 幂等键同 payload 返回原对象，不同 payload 拒绝；并发终态冲突拒绝 |
| EP09-AC-011 | P0 | 所有状态变化写入可验证 M10 hash-chain audit，历史不可覆盖 |
| EP09-AC-012 | P0 | 输出无外发、订单、付款、供应商选择或商业承诺能力 |
| EP09-AC-013 | P0 | Ruff、strict mypy、模块测试及 EP-07/EP-08/M10 回归通过 |
