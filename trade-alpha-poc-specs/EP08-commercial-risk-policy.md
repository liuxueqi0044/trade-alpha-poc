# EP-08：商业、安全与交易风险策略

状态：Accepted for executable PoC
上游：EP-03、EP-04、EP-06、EP-07、M10
下游：EP-09、EP-10

## 1. 职责与边界

EP-08 将冻结规格、供应商资格、已确认报价、可比性问题和成本场景放入版本化、deny-by-default 的确定性策略中，输出逐项风险 finding 和内部 dry-run disposition。

EP-08 不是法律、税务、制裁、信用或保险意见；不选择供应商，不产生客户报价、订单、付款或外发授权。未知专业结论只能变成 `needs_evidence` 或 `requires_specialist_review`。

## 2. 强制规则

- 只接受 repository-saved 的精确 frozen 模型；拒绝 duck type、ID/范围错配和伪造指纹。
- hard exclusion、过期资格、无效报价、quarantine、未来数据和不完整成本不能被总分或人工普通决定抵消。
- unknown、missing、stale 和 provider error 均不得视为 clear。
- 每个 finding 都有稳定 code、severity、dimension、输入 refs、证据 refs 和建议动作。
- 人工决定逐 finding 追加，只能处理 policy 明确允许的 `approval_required` 或 warning；hard block 必须通过新证据和新 assessment 关闭。
- policy 内容、有效期、规则和允许角色均版本化并带 canonical fingerprint。
- 评估与人工决定写入 M10 append-only hash-chain audit；SecretScanner 不得回显敏感内容。

## 3. 首版对象和服务

- `RiskPolicyVersion`
- `RiskSeverity`
- `RiskDisposition`
- `RiskFinding`
- `RiskDecision`
- `TradeRiskAssessmentSnapshot`
- `TradeRiskService`
- `TradeRiskRepositoryPort`
- `TradeRiskSourceReader`
- `ExecutionAuditPort`

## 4. 首版规则族

- safety：EP-03 hard exclusion、安全范围未知、spec 未冻结。
- supplier：EP-04 资格过期/阻断、筛查 stale/error/candidate、身份未解决。
- quote：报价过期、quarantine、行/规格/供应商错配、Incoterm/named place/MOQ/有效期问题。
- cost：required component 未知、FX 不可用、最低价格或毛利无法计算、policy margin floor 不满足。
- transaction：DDP、账期、目的地/路线、税务/许可/收付款身份缺证据时触发 block 或 specialist review。

## 5. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP08-AC-001 | P0 | 相同输入/策略/as_of 幂等；策略变化产生新 assessment |
| EP08-AC-002 | P0 | 缺快照、错误类型/ID/范围/指纹和未知 policy 均 fail-closed |
| EP08-AC-003 | P0 | 未来输入或未来决定不可进入历史 as_of |
| EP08-AC-004 | P0 | unknown、stale、quarantine、provider error 和许可 deny 不得 clear |
| EP08-AC-005 | P0 | EP-03 hard exclusion 与安全 block 不可被其他正面信号抵消 |
| EP08-AC-006 | P0 | EP-06 issue 与 EP-07 unresolved/margin 问题映射为证据化 finding |
| EP08-AC-007 | P0 | 人工决定逐 finding、角色授权、带理由/时间/审计且不能覆盖 hard block |
| EP08-AC-008 | P0 | 输入或新证据变化必须产生新 assessment，旧快照和决定不可覆盖 |
| EP08-AC-009 | P0 | 最早上游有效期约束 assessment validity |
| EP08-AC-010 | P0 | 输出无 winner、supplier selection、customer quote、order 或 outbound authority |
| EP08-AC-011 | P0 | M10 audit chain 完整且敏感内容不回显 |
| EP08-AC-012 | P0 | Ruff、strict mypy、模块测试及 EP-03～EP-07 回归通过 |

实现边界：评估服务只接受由 `RepositoryTradeRiskSourceReader` 按 exact ID 从上游 repository 重载的规格、资格、报价、归一化、比较和成本快照；调用方预装或仅自称 fingerprint 一致的对象不能越过该边界。
