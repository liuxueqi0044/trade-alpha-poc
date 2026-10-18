# EP-03：产品规格解析与版本化

状态：Verified for executable PoC
上游：EP-01、EP-02、M03
下游：供应商资格、供应商 RFQ、报价比较

## 1. 职责与边界

EP-03 将 RFQ 候选映射为 M03 Claim，执行类型和单位归一、等价分组、冲突检测、缺失判断、硬排除检查、人工决定和不可变规格版本冻结。

冻结只表示后续模块可以引用该规格版本，不代表允许发送、报价、付款或承诺。

## 2. 强制规则

- 原始值、类型化值和规范值同时保留。
- Claim accepted 只表示证据确实如此表达，不表示该值已被选为最终规格。
- 等价单位可以分组，但原始 Claim 不得合并或删除。
- 数值差异默认构成冲突；只有版本化产品配置显式声明公差时才允许等价。
- 来源优先级和模型置信度不能自动解决关键尺寸、材料、性能和安全冲突。
- `missing`、`unknown`、`not_applicable`、`open_to_supplier_proposal` 必须区分。
- 冻结必须由授权人工触发；冻结版本不可编辑，只能被新版本替代。
- 硬排除用途命中后状态为 blocked。

## 3. 首版对象和服务

- `ProductFamilyProfile`
- `FieldDefinition`
- `ProductSpec`
- `SpecFieldProjection`
- `SpecConflict`
- `SpecFieldDecision`
- `ProductSpecVersion`
- `ReadinessResult`
- `SpecificationService`
- `ProductSpecRepositoryPort`
- `ClaimRepositoryPort`

## 4. FE-DRY-01 范围门

- 粉尘毒性、可燃性、ATEX/防爆范围未知时不能进入供应商 RFQ。
- 确认命中可燃/有毒粉尘、防爆、食品、医疗、呼吸、消防、承压或完整设备时必须 blocked。
- 通过 OEM/型号/照片/图纸或完整尺寸组合形成可识别 SKU。
- 首单或阶梯询价数量明确。
- 材料、性能和包装未知项必须补齐或经人工明确标记 `open_to_supplier_proposal`。

## 5. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP03-AC-001 | P0 | 每个规格字段可回到 Claim、DocumentBlock 和 Artifact |
| EP03-AC-002 | P0 | `25.4 mm` 与 `1 inch` 等价分组且保留两条原始 Claim |
| EP03-AC-003 | P0 | `325 mm` 与 `330 mm` 产生冲突，不静默覆盖 |
| EP03-AC-004 | P0 | 粉尘毒性/可燃性未知时 gate 为 needs_clarification |
| EP03-AC-005 | P0 | 命中硬排除范围后状态为 blocked |
| EP03-AC-006 | P0 | 人工解决冲突生成新决定和新版本，旧版本不变 |
| EP03-AC-007 | P0 | 相同输入重跑不新增版本；配置/规则变化产生新版本 |
| EP03-AC-008 | P0 | 冻结版本包含 profile、claim、decision、normalizer 和内容指纹 |
| EP03-AC-009 | P0 | 支持历史时间点重放和字段级 diff |
| EP03-AC-010 | P0 | 冻结不能触发外部发送，只能由后续授权模块消费 |
| EP03-AC-011 | P0 | Ruff、strict mypy、模块测试及 M03 回归通过 |
