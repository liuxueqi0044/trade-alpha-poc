# ADR-015：开源复用优先与总体成本门

状态：Accepted
日期：2026-08-26

## 决策

实现前按以下顺序选择方案：仓库现有代码、成熟开源适配器、最后才是本地薄实现。存在开源项目并不自动意味着采用；只有集成、改造、运行、许可、安全、维护和退出总成本低于薄实现时才引入。

## EP-01～EP-03 选择

- 继续使用 Pydantic、Decimal、M01 EvidenceStore、M02 ExtractionService、M03 Claim/KnowledgeStore 和 RapidFuzz。
- 使用 Pint 做物理单位验证和换算，通过端口隔离，不持久化 Pint 对象。
- 使用 Pydantic Extra Types 的 ISO 4217 类型，并锁定其 `pycountry` extra。
- Docling 保持可选复杂文档后端，不与第二套通用解析框架并行作为默认路径。
- 不引入 py-moneyed、Unstructured、Tika、extract-msg、完整 ERP 或 PIM。

## 引入门

新依赖必须有：明确责任、许可证记录、版本上限、黄金样本、失败降级、可替换端口和退出策略。不得为了使用开源项目而 fork 大型代码库并形成比薄实现更重的永久维护负担。
