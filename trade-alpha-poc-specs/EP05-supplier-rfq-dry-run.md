# EP-05：供应商 RFQ 包与零外发演练

状态：Verified for executable PoC
上游：EP-01、EP-03、EP-04
下游：EP-06

## 1. 职责与边界

EP-05 为 campaign 内每一家供应商生成隔离、可审计的结构化 RFQ 包与本地派生视图。成功是 `rendered_not_sent` 且证明副作用为零，不是供应商收到文件。

## 2. 强制规则

- 只消费冻结规格和精确、有效、指纹一致的 EP-04 资格快照。
- supplier 必须属于 campaign；每家包隔离且不泄漏买家敏感信息或其他供应商。
- 模板使用 Jinja2 sandbox/StrictUndefined/allow-list view，不执行业务计算。
- 附件必须白名单、hash 有效且 metadata 已净化；失败时清空并阻断。
- 唯一 outbound 为 exact `NullOutboundAdapter`；状态中无 sent/delivered 成功路径。
- terminal 包同输入幂等，任一资格/规格/模板/policy/附件变化必须新包或 fail-closed。

## 3. 独立验收

EP05-AC-001～013 以 `docs/ep04-ep06-detailed-architecture.md`/原详细架构第 6.8 节为准：覆盖规格/资格门、隔离披露、模板、幂等版本、附件安全、Null outbound 与回归。
