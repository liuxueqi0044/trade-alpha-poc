# M00：全局架构基线与变更治理

状态：Accepted for PoC design  
所有者：Architecture  
消费者：M01–M10

## 1. 系统边界

系统负责：

- 取得、保存并版本化外部证据；
- 将证据解析成带出处的候选声明；
- 建立商品、国家、企业、设施、文件、事件和关系；
- 在 point-in-time 约束下计算特征、信号和机会排名；
- 管理研究资源与真实业务实验；
- 保存每次判断、行动和结果的审计链。

系统不负责：

- 未经人工授权向客户发送消息、承诺价格或下单；
- 将模型输出直接视为法律、合规或商业事实；
- 替代正式法律、税务、制裁或信用审查；
- 在没有数据许可的情况下复制、再分发或商业化第三方数据。

## 2. 全局不变量

这些规则高于具体实现，任何模块不得破坏。

| ID | 不变量 |
|---|---|
| INV-001 | 原始证据 append-only；解析和修订不得覆盖历史内容。 |
| INV-002 | 每条事实、特征、信号和决策必须可追溯到输入快照、代码版本和配置版本。 |
| INV-003 | 回测只能读取 `observed_at <= as_of` 且当时系统版本有效的数据。 |
| INV-004 | 商品宇宙、国家宇宙和排除条件必须配置化，不能在业务代码中写死“选中的产品”。 |
| INV-005 | LLM/OCR/规则抽取结果默认是候选声明，不是已确认事实。 |
| INV-006 | 同一输入、代码、配置和依赖版本必须产生内容等价且 hash 稳定的确定性产物；明确标记的随机模型除外。 |
| INV-007 | 连接器和任务必须幂等；重试不能制造重复事实或重复业务动作。 |
| INV-008 | 不完整、质量失败或许可状态不明的数据不能发布到 gold 层。 |
| INV-009 | 软件许可、模型许可、API 条款和数据许可分别登记。 |
| INV-010 | 实体合并必须可解释、可撤销，并保留被合并记录的原始身份。 |
| INV-011 | 研究分数必须可分解；总分不能成为无法解释的单一黑箱输出。 |
| INV-012 | 架构成功与是否发现 Alpha 分开验收；“没有统计优势”是合法研究结论。 |
| INV-013 | 任何对外业务动作必须由明确授权的人工或策略状态机触发，并具有最大损失/额度约束。 |
| INV-014 | 所有时间以 UTC 保存；来源本地时间和时区作为附加字段保留。 |
| INV-015 | 所有金额同时保存原币、币种、换算值、汇率来源与换算日期。 |

## 3. 统一对象标识

| 对象 | ID 规则 |
|---|---|
| Source | 人工分配稳定 `source_id`，不可因 URL 改变而复用给其他来源 |
| Artifact | UUID + 内容 `sha256`；相同内容可被多个 retrieval 引用 |
| Retrieval | 每次请求一个 UUID；即使内容相同也保留本次获取记录 |
| Claim | UUID；包含 subject/predicate/object/evidence 和版本 |
| Entity | 系统稳定 UUID；来源记录 ID 不作为主实体 ID |
| Observation | 来源自然键 + vintage/version；不把最新值作为唯一记录 |
| Feature | `feature_name + feature_version + entity/opportunity + as_of` |
| Signal | `signal_definition_version + opportunity + as_of` |
| Experiment | UUID；定义一轮冻结 universe、特征、成本和动作预算的研究 |
| Decision/Action | UUID；必须关联 experiment、actor、输入 snapshot 和权限 |

## 4. 跨模块事件信封

模块之间传递事件时至少包含：

```yaml
event_id: uuid
event_type: artifact.retrieved
occurred_at: 2026-08-24T10:30:00Z
emitted_at: 2026-08-24T10:30:02Z
producer: m01.ingestion
producer_version: 0.1.0
correlation_id: uuid
causation_id: uuid-or-null
subject_id: artifact-uuid
payload_schema: artifact-retrieved/1.0.0
payload_uri: manifests/events/date=2026-08-24/...
payload_hash: sha256:...
```

PoC 可以由 Dagster 内部事件实现，不要求先部署 Kafka；事件语义必须保持。

## 5. 版本策略

- 架构规范使用语义版本：`MAJOR.MINOR.PATCH`；
- schema、特征、信号、模型、prompt 和数据源解析器分别版本化；
- 改字段含义、时间语义或主键属于破坏性变更；
- 新增可选字段属于兼容变更；
- 仅文字澄清且不改变行为属于 patch；
- 历史结果保留其原始版本引用，不进行静默“升级”。

## 6. ADR 规则

以下变化必须建立 ADR：

- 替换 Dagster、DuckDB、PostgreSQL 等核心组件；
- 改变 raw/bronze/silver/gold 的职责；
- 改变 point-in-time 规则；
- 改变实体自动合并阈值或决策原则；
- 改变评分、成本模型或回测 universe 定义；
- 弱化任何 P0 验收条件；
- 新增会产生外部状态变化的自动化动作。

ADR 模板：

```markdown
# ADR-NNN：标题
状态：Proposed/Accepted/Superseded
日期：YYYY-MM-DD
背景：为什么需要决定
决定：选择什么
备选：拒绝了什么以及原因
影响：数据、接口、迁移、许可、成本和验收变化
替代：若失败如何回退
关联：模块、schema、验收项、代码提交
```

## 7. 全局完成定义

一个模块不能仅因“代码能跑”而完成。它必须同时满足：

- 功能正确；
- 输入输出契约已版本化；
- 原始证据和 lineage 可追溯；
- 幂等、失败恢复和重放通过；
- point-in-time 规则通过；
- 安全与许可检查通过；
- 指标、日志和告警存在；
- P0 验收证据被保存到 `artifacts/acceptance/<module>/<version>/`。

## 8. M00 独立验收标准

| ID | 优先级 | 验收场景 | 通过条件 | 证据 |
|---|---|---|---|---|
| M00-AC-001 | P0 | 文档索引完整 | M00–M10 和追踪矩阵均可从 README 到达，无断链 | 链接检查报告 |
| M00-AC-002 | P0 | 不变量可追踪 | 每条 INV 至少映射到一个模块验收项或明确标记为架构评审项 | 追踪矩阵 |
| M00-AC-003 | P0 | 版本可辨识 | 所有 schema、特征、信号、prompt 和模型产物都含非空版本字段 | schema 检查报告 |
| M00-AC-004 | P0 | 破坏性变更治理 | 构造一次时间字段语义变更；CI 在无 ADR 时失败，有 Accepted ADR 时通过 | CI 日志 |
| M00-AC-005 | P0 | 对外动作受控 | 在无授权 token/人工确认时，系统无法产生任何外部消息或订单动作 | 安全集成测试 |
| M00-AC-006 | P1 | 文档与实现偏差 | 自动生成组件清单与规范对比，未登记组件使 CI 告警 | 架构漂移报告 |

