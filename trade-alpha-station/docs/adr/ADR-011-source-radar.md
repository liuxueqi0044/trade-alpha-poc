# ADR-011：在 M01 前增加 M11 数据源雷达

状态：Accepted  
日期：2026-08-25

## 决策

增加 M11 Source Discovery & Onboarding。M11 使用轻量 CKAN 和 APIs.guru adapter、curated seed
catalog、追加式 candidate registry、安全元数据 fetcher、可解释优先级和 dlt/M01 connector 草案。

M11 不直接启用来源。完整顺序固定为：

```text
discover → probe → M10 policy review → disabled connector draft → M05 Golden → M01 enable
```

目录元数据、OpenAPI 定义、软件和数据许可证分别登记。任何未知项 deny by default。

## 理由

CKAN/DCAT 和 APIs.guru 解决外部目录/API 的广度；dlt 解决 REST/OpenAPI connector 生成；现有
M10/M01 已具备许可和不可变证据门。当前候选数量不足以证明部署 Magda/OpenMetadata/DataHub 的
持续运维成本合理。

## 后果

- 新增 `SourceCandidate`、`PolicyReview`、`GoldenValidation` 和 `OnboardingBundle` 合同；
- M01 `SourceRecord` 语义不变，M11 只生成兼容草案；
- 来源条款变化会把候选退回 policy review；
- 需要持久化时，用 PostgreSQL 替换内存 candidate registry，不改变状态机；
- 若未来部署 AGPL 的 ckanext-dcat 或修改后的网络服务，必须重新执行 M10 软件许可评审。

