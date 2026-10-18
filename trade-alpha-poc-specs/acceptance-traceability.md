# 跨模块验收追踪矩阵

版本：0.3.0
目的：将全局不变量、模块接口、验收项和证据产物建立稳定映射，避免后续实现依赖聊天记忆。

## 1. 全局不变量追踪

| 不变量 | 主要执行模块 | 主要验收项 |
|---|---|---|
| INV-001 原始证据不可覆盖 | M01、M04 | M01-AC-004/005，M04-AC-006 |
| INV-002 全链可追溯 | M02、M05、M06、M08、M10 | M02-AC-002，M05-AC-008，M06-AC-006，M08-AC-002，M10-AC-008 |
| INV-003 Point-in-time | M03–M07 | M03-AC-007，M04-AC-001，M05-AC-004，M07-AC-003/004 |
| INV-004 Universe 配置化 | M07 | M07-AC-001/002 |
| INV-005 候选声明不等于事实 | M02、M03、M08 | M02-AC-003/012，M03-AC-006/008，M08-AC-003 |
| INV-006 可重复产物 | M03–M07 | M03-AC-001/011，M04-AC-003，M05-AC-012，M07-AC-008 |
| INV-007 幂等 | M01、M06、M09 | M01-AC-002/003，M06-AC-003/007，M09-AC-004 |
| INV-008 失败数据不发布 | M01、M04–M06 | M01-AC-006，M04-AC-002，M05-AC-001/006，M06-AC-002 |
| INV-009 许可分别登记 | M01、M10 | M01-AC-007，M10-AC-001/002/011 |
| INV-010 实体合并可撤销 | M03、M06 | M03-AC-002/005，M06-AC-010 |
| INV-011 分数可分解 | M07、M08 | M07-AC-005，M08-AC-002/004 |
| INV-012 无 Alpha 是合法结论 | M07 | M07-AC-007/010 |
| INV-013 外部动作受权 | M00、M07、M09、M10 | M00-AC-005，M07-AC-013，M09-AC-002/003/008，M10-AC-004/013 |
| INV-014 UTC 时间 | M04 | M04-AC-011 |
| INV-015 金额和汇率 | M04、M05、M09 | M04-AC-010，M05-AC-010，M09-AC-007 |
| INV-016 发现不等于批准 | M11、M10、M01 | M11-AC-004/005/006/007，M10-AC-001/002，M01-AC-007 |
| INV-017 交易值显式单位/币种 | EP-01、EP-03 | EP01-AC-002/003/004，EP03-AC-002/003 |
| INV-018 RFQ 候选不等于确认规格 | EP-02、EP-03 | EP02-AC-004/005，EP03-AC-001/006 |
| INV-019 冻结规格不等于外部授权 | EP-03、M10 | EP03-AC-010，M10-AC-004/013 |
| INV-020 未知成本不等于零 | EP-06、EP-07 | EP06-AC-008/009，EP07-AC-003/004 |
| INV-021 机器比较不等于供应商选择 | EP-06、EP-07、EP-10 | EP06-AC-012，EP07-AC-010，EP10-AC-002 |
| INV-022 内部批准不等于真实外发授权 | EP-09、EP-10、M10 | EP09-AC-003/004/012，EP10-AC-010/011，M10-AC-013 |

## 2. 跨模块接口契约

| 接口 ID | 生产方 | 消费方 | 核心对象 | Schema owner | 兼容性规则 |
|---|---|---|---|---|---|
| IF-001 | M01 | M02/M04/M06 | RetrievalEnvelope、ArtifactManifest | M01 | 新增可选字段兼容；hash/时间语义变更为 breaking |
| IF-002 | M02 | M03/M05/M08 | DocumentBlock、CandidateClaim | M02 | locator、artifact_id、parser version 不可删除 |
| IF-003 | M03 | M05/M07/M08 | Entity、EntityLink、Relationship、Event | M03 | stable entity ID；resolution version 必填 |
| IF-004 | M04 | M05/M07/M08 | SnapshotManifest、PIT Query Contract | M04 | snapshot 不可变；alias 可原子移动 |
| IF-005 | M05 | M07/M08 | Published Mart、FeatureInput | M05 | 粒度、单位、主键、availability lag 变更为 breaking |
| IF-006 | M06 | 全部 | RunManifest、AssetEvent | M06 | code/config/input/output/quality 字段不可缺失 |
| IF-007 | M07 | M08/M09 | OpportunitySnapshot、Allocation、ExperimentProposal | M07 | score decomposition、as_of、版本和约束必填 |
| IF-008 | M08 | M03/M09/M10 | ReviewDecision、UserProposal、ExportAudit | M08 | actor、input version、reason、time 必填 |
| IF-009 | M09 | M03/M05/M07 | BusinessEvent、Outcome、Cost | M09 | 不允许 hard delete；event version 和 idempotency 必填 |
| IF-010 | M10 | 全部 | PolicyDecision、Identity、VersionManifest | M10 | deny 默认；未知许可/身份不能自动 allow |
| IF-011 | M11 | M10/M01 | SourceCandidate、PolicyReview、OnboardingBundle | M11 | 候选不可直接调度；只有完整人工审批可生成 enabled bundle |
| IF-012 | EP-02 | EP-03 | Rfq、RfqLineItem、RfqFieldCandidate | EP-02 | 候选必须保留 artifact/block 定位；不能出现 confirmed |
| IF-013 | EP-03 | 后续询价/报价模块 | ProductSpecVersion、ReadinessResult | EP-03 | 冻结版本不可修改；下游必须引用精确 version id |
| IF-014 | EP-04 | EP-05 | SupplierQualificationSnapshot | EP-04 | exact type/scope/PIT/fingerprint；dry-run eligibility 不代表外联授权 |
| IF-015 | EP-05 | EP-06 | SupplierRfqPackageSnapshot | EP-05 | 每供应商隔离；rendered_not_sent 不代表已发送 |
| IF-016 | EP-06 | EP-07 | SupplierQuoteSnapshot、QuoteNormalizationRun、SupplierComparisonMatrix | EP-06 | confirmed 不代表已接受；未知成本边界不得丢失 |
| IF-017 | EP-07 | EP-08 | CostScenarioSnapshot | EP-07 | 每场景只绑定一份 quote/line/tier；无 selection 语义 |
| IF-018 | EP-08 | EP-09 | TradeRiskAssessmentSnapshot | EP-08 | hard block 不可普通 override；clear 只限内部 dry-run |
| IF-019 | EP-09 | EP-10 | ReadinessEvaluationSnapshot、ApprovalRequest | EP-09 | approval 绑定精确 resource fingerprint；production action 始终受硬门约束 |

## 3. 跨模块验收项

| ID | 优先级 | 场景 | 参与模块 | 通过条件 | 证据 |
|---|---|---|---|---|---|
| X-AC-001 | P0 | Artifact 到 Claim | M01→M02 | 从 fixture URL/PDF 取得 raw，生成 block/claim；claim 能返回同一 raw hash 和精确 locator | E2E 证据链报告 |
| X-AC-002 | P0 | Claim 到 Entity | M02→M03 | 两个来源名称进入实体解析；自动/人工决策符合阈值并保留 claim | entity E2E 报告 |
| X-AC-003 | P0 | Vintage 到 Feature | M01/M04/M05→M07 | 两个数据 vintage 在不同 as-of 下产生对应特征，不发生未来泄漏 | PIT E2E 报告 |
| X-AC-004 | P0 | Feature 到证据 UI | M05/M07→M08 | Top-K 行可下钻到特征输入、claim/observation、artifact | UI traversal 报告 |
| X-AC-005 | P0 | 机会到实验 | M07/M08→M09 | 只有 approved 策略和授权用户能创建有限实验；预算/止损冻结 | 状态机 E2E |
| X-AC-006 | P0 | 实验到标签 | M09→M03/M05/M07 | 失败/无响应/成功事件均作为新 outcome 进入后续评估，不改旧榜单 | feedback replay |
| X-AC-007 | P0 | 修订影响传播 | M01→M04→M05→M06→M07/M08 | 来源修订只重算受影响分区，发布新 snapshot，旧报告不变 | lineage/backfill 报告 |
| X-AC-008 | P0 | 实体撤销传播 | M03→M06→M05/M07/M08 | 撤销 link 后正确重算，UI 新旧 snapshot 分别展示当时结果 | rollback E2E |
| X-AC-009 | P0 | 许可阻断全链 | M10→M01/M04/M08 | 将来源改为禁止商业使用后，新采集/受限读取/导出按策略阻断，历史审计保留 | policy E2E |
| X-AC-010 | P0 | 固定快照全链重放 | M01–M10 | 固定 raw、配置、代码和环境从零重建，gold、排名和 version manifest hash 一致 | full replay report |
| X-AC-011 | P0 | 故障恢复 | M01/M02/M04/M06 | 在分页、OCR、gold 发布三处分别注入故障；均无重复、无半发布、可恢复 | chaos report |
| X-AC-012 | P0 | 权限纵深 | M08/M09/M10 | 绕过 UI 直接调用操作层也不能读取受限证据或执行未批准动作 | authorization report |
| X-AC-013 | P0 | 候选到正式来源 | M11→M10→M01 | 发现候选默认禁用；许可不完整时 M01 调用为 0；完整人工审批后才可登记，Golden 通过前仍不得正式调度 | source onboarding E2E |
| X-AC-014 | P0 | 买家来件到冻结规格 | M01/M02→EP-02→M03/EP-03 | 脱敏 RFQ 的正文和附件先进入不可变证据；候选字段保留 locator；单位等价和冲突正确；人工决定后形成冻结版本；全程无外部动作 | RFQ-to-spec replay |
| X-AC-015 | P0 | 冻结规格到供应商比较 | EP-03→EP-04→EP-05→EP-06 | 三供应商资格、隔离 RFQ、三格式报价、人工确认、normalization 和 matrix 可重放；零外发、零选择 | supplier quote replay |
| X-AC-016 | P0 | 报价到成本场景 | EP-06→EP-07 | 每份 quote 的产品/补充成本、FX、数量分摊和未知边界可追溯；不产生 winner | costing replay |
| X-AC-017 | P0 | 成本到风险 | EP-03/EP-04/EP-06/EP-07→EP-08 | hard exclusions、过期/未知、成本和 margin 问题生成确定性 findings，不能被普通决定抵消 | risk replay |
| X-AC-018 | P0 | 风险到四眼批准 | EP-07/EP-08→EP-09→M10 | exact resource 通过 maker/reviewer/approver 分离、RBAC、PIT 和 hash audit；生产动作仍 blocked | approval replay |
| X-AC-019 | P0 | 已批 basis 到客户报价包 | EP-09→EP-10 | 人工选择场景并逐项确认价格/条款，安全渲染本地报价与沟通草稿；无供应商/成本泄漏和外发能力 | quote package replay |
| X-AC-020 | P0 | RFQ 到可人工审核报价包 | EP-01～EP-10、M01～M03、M10 | 固定匿名案例从 raw RFQ 重放到 READY_FOR_HUMAN_APPROVAL 或有原因 BLOCKED；全链指纹/audit 有效，零发送/订单/付款 | execution-plane full replay |

## 4. 验收阶段门

```mermaid
flowchart LR
    G0[G0 规范基线] --> G1[G1 证据链]
    G1 --> G2[G2 理解与知识]
    G2 --> G3[G3 数据发布]
    G3 --> G4[G4 研究与回测]
    G4 --> G5[G5 UI与人工复核]
    G5 --> G6[G6 有限真实实验]
```

### G0：规范基线

必须通过：M00-AC-001–005。  
产物：版本化 schema、ADR 流程、模块所有者和验收目录。

### G1：证据链

必须通过：M01 P0、M04-AC-005/006、M10-AC-001/003/009、X-AC-001。  
产物：可追溯、不可变、合规 gate 的 raw 获取链。

### G2：理解与知识

必须通过：M02 P0、M03 P0、X-AC-002。  
产物：Golden document 结果、claim、entity/link 和人工复核。

### G3：数据发布

必须通过：M04 P0、M05 P0、M06 P0、X-AC-003/007/011。  
产物：原子、可重复、point-in-time 的 published snapshot。

### G4：研究与回测

必须通过：M07 P0、X-AC-010。  
产物：无泄漏基线、透明评分、walk-forward 和 null simulation。

### G5：UI 与人工复核

必须通过：M08 P0、X-AC-004/008/012。  
产物：机会矩阵、证据下钻、RBAC 和审核审计。

### G6：有限真实实验

必须通过：M09 P0、M10 P0、X-AC-005/006/009。  
产物：有预算、止损、人工批准和反馈回流的第一轮实验。

后一个 gate 不得在前一个 gate 未通过时被宣布完成。

## 5. 验收证据规范

```text
artifacts/acceptance/
  <spec-version>/
    M00/
    M01/
    ...
    M10/
    cross-module/
      X-AC-001/
      ...
```

每个验收目录至少包含：

```yaml
acceptance_id: M01-AC-002
spec_version: 0.1.0
status: passed|failed|waived
executed_at: ...
executor: user-or-ci
code_sha: ...
environment_lock_hash: ...
fixture_version: ...
command_or_test: ...
input_snapshot_ids: [...]
evidence_files: [...]
result_hash: ...
waiver_adr: null
```

截图可以作为辅助证据，不能替代机器可读测试结果。

## 6. Fixture 与基准集

必须冻结以下 fixture：

| Fixture ID | 内容 | 主要覆盖 |
|---|---|---|
| FX-API-001 | 三页 API，第二次执行内容不变 | 幂等、分页 |
| FX-API-002 | 第三页首次 500、重试成功 | 恢复 |
| FX-VINTAGE-001 | 同一 period 的 v1/v2 修订 | point-in-time |
| FX-HTML-001 | 静态政策页面及一次局部变化 | 网页抽取/diff |
| FX-PDF-001 | 数字多栏法规和跨页表格 | Docling、locator |
| FX-SCAN-001 | 中英文扫描证书 | OCR |
| FX-ENTITY-001 | 多语言同实体、同名不同实体、母子公司 | 实体解析 |
| FX-INJECTION-001 | 文档 prompt injection、CSV 公式、恶意重定向 | 安全 |
| FX-NOALPHA-001 | 已知无预测关系的合成面板 | 研究诚实性 |
| FX-BUSINESS-001 | 成功、失败、无响应、质量问题事件 | 反馈闭环 |

Fixture 变更必须创建新版本；不能为了让测试通过而覆盖历史基准。

## 7. 例外与豁免

- P0 验收默认不可豁免；如确需豁免，必须有 Accepted ADR、owner、原因、风险缓解和到期日；
- P1 可以延后，但必须出现在发布说明的已知缺口中；
- 任何豁免不得使未授权外部动作、未来泄漏、原始证据篡改或许可违规变为允许状态；
- 到期豁免自动转为失败，不默认为续期。
