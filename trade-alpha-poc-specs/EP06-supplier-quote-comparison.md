# EP-06：供应商报价证据、归一化与可比性

状态：Verified for executable PoC
上游：EP-01、EP-03、EP-05、M01、M02
下游：EP-07

## 1. 职责与边界

EP-06 保存模拟供应商报价原件，生成候选，经人工逐字段/逐行确认后冻结 SupplierQuoteSnapshot，再用明确数量、单位、FX、Incoterm 和有效期政策生成 normalization 与 comparison matrix。

## 2. 强制规则

- adapter receipt 必须与提交 bytes、metadata、artifact、fingerprint 和 quarantine 绑定。
- 抽取候选不能直接成为事实；所有报价行、价格和非空商业条款需匹配人工决定。
- quote 精确绑定 supplier RFQ package、profile、spec、receipt evidence 和 lineage。
- normalization 只用 Decimal、明确 unit/quantity/FX；未来 FX 或快照不得进入历史 as_of。
- included/excluded/unknown/separately quoted/not applicable 语义分离，unknown 不等于 0。
- matrix 暴露不可比维度与 unresolved cost，不包含 winner、selection 或采购批准。

## 3. 独立验收

EP06-AC-001～014 以 `docs/ep04-ep06-detailed-architecture.md`/原详细架构第 7.11 节为准：覆盖三格式证据、M02 下钻、人工确认、Decimal normalization、FX/PIT、Incoterm/成本边界、矩阵幂等与零选择。
