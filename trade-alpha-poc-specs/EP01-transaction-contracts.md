# EP-01：交易领域契约

状态：Verified for executable PoC
上游：M00、M01–M03 公共契约
下游：EP-02、EP-03 及后续报价/成本模块

## 1. 职责与边界

EP-01 定义金额、数量、单位、范围、公差、汇率、Incoterms、证据来源、人工决定和稳定指纹。模块只含不可变领域契约与纯计算，不进行文件、数据库、网络或外部动作。

## 2. 强制规则

- 复用 Pydantic v2、`Decimal`、`VersionRef`、`EvidenceLocator` 和 UTC 工具。
- 金额、数量和汇率不得经过二进制 float。
- 币种必须是显式 ISO 4217 代码。
- 单位持久化为 `Decimal + unit code`；Pint 只存在于适配器后。
- 跨币种运算必须显式提供带来源和观察时间的汇率快照。
- Incoterm 必须包含规则版本和 named place。
- 公共模型统一 frozen、extra forbid、版本化、稳定 JSON 序列化。

## 3. 首版对象

- `MonetaryAmount`
- `MeasuredValue`
- `MeasuredRange`
- `Tolerance`
- `FxSnapshot`
- `Incoterm`
- `ProvenanceRef`
- `HumanDecision`
- `UnitConversionPort`
- `CanonicalFingerprintPort`

## 4. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP01-AC-001 | P0 | 全部公共值对象稳定 JSON round-trip |
| EP01-AC-002 | P0 | 非法币种、未知单位、跨维度换算和无地点 Incoterm 被拒绝 |
| EP01-AC-003 | P0 | `25.4 mm == 1 inch` 的转换不经过 float |
| EP01-AC-004 | P0 | 无 `FxSnapshot` 时跨币种运算被拒绝 |
| EP01-AC-005 | P0 | 同一对象在不同运行中生成相同 SHA-256 指纹 |
| EP01-AC-006 | P0 | contracts 包不依赖 RFQ、文件、网络或数据库实现 |
| EP01-AC-007 | P0 | Ruff、strict mypy、单元测试和 JSON Schema 契约夹具通过 |
