# EP-04：供应商主档、能力声明与资格门

状态：Verified for executable PoC
上游：EP-01、EP-03、M01～M03、M10
下游：EP-05

## 1. 职责与边界

EP-04 保存版本化供应商身份、site、identifier、能力/文件声明、筛查结果、资格评估、人工决定和不可变资格快照。资格只允许进入内部 dry-run，不代表法律尽调完成、允许真实联系或下单。

## 2. 强制规则

- 相似名称只生成候选，不自动合并。
- identifier 格式有效与注册处核验分离。
- 能力绑定 supplier、site、产品族、spec scope、来源和有效期。
- 过期/未人工审核文件和 stale/error screening 不得产生安全通过。
- assessment 与 snapshot 精确绑定 ProductSpecVersion、policy 内容指纹和 as_of。
- hard scope conflict 不能被总分抵消；人工决定必须授权且 append-only。

## 3. 独立验收

EP04-AC-001～013 以 `docs/ep04-ep06-detailed-architecture.md`/原详细架构第 5.7 节为准：覆盖幂等、身份候选、identifier/site/capability/document/screening、policy/role、PIT snapshot、指纹与零外发回归。
