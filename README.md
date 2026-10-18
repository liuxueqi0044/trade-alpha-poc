# Trade Alpha PoC

一个以证据链、point-in-time 数据和人工审批为核心的“量化外贸”端到端 PoC。项目把全球贸易数据、网页与文档证据、实体解析、研究评分、商业验证和治理控制组织成一条可审计链路。

## 当前状态

- 工程 PoC：M01–M11 与执行平面 EP01–EP06 已形成可执行参考实现和独立验收测试。
- 产品—市场研究：首轮选择为德国工业 MRO/过滤服务渠道 × 中国非承压、非安全关键工业除尘滤筒；荷兰为第二市场。
- 商业验证：买家与供应商候选池已建立，但尚未外联、询价、拿样、签约或获得订单。
- 公司设立：运营主体及注册地址条件仍待核验。

完整进度与决策边界见 [项目状态报告](PROJECT-STATUS-2026-08-26.md)。

## 仓库内容

- [`trade-alpha-station/`](trade-alpha-station/)：M01–M11 Python 可执行 PoC、测试、配置和验收产物。
- [`trade-alpha-poc-specs/`](trade-alpha-poc-specs/)：11 个模块的冻结规格与独立验收标准。
- [`trade-alpha-poc-complete-architecture.md`](trade-alpha-poc-complete-architecture.md)：端到端总体架构。
- [`open-source-trade-intelligence-landscape.md`](open-source-trade-intelligence-landscape.md)：79 个开源项目的调研与选型。

## 重要边界

本仓库证明的是本地 PoC 的接口契约、证据追溯、时间语义、安全边界和参考执行路径，不证明已接入生产数据、已找到可成交 Alpha、已取得监管许可或已完成真实贸易闭环。所有外联、采购、付款、签约、认证和订单动作均保留人工审批。

