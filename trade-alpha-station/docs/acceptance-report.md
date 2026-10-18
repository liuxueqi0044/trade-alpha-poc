# M01–M11 + EP01–EP10 PoC 验收报告

日期：2026-08-27
范围：本地确定性 PoC、Golden/故障/并发/安全 fixture  
结论：**235 passed；Ruff clean；133 source files strict mypy clean；165 packages compatible**

## 0. EP04–EP10 当前终审

EP04–EP06 完成供应商资格、零外发 RFQ 包、供应商报价接入/人工确认/归一化/可比性；EP07–EP10
完成证据化成本与毛利、deny-by-default 风险 finding、准备度与 M10 三角色审批、客户本地报价草稿
和 EP01–EP10 manifest。SOL 冻结架构并完成跨模块适配/终审，Terra/Luna 分包实现，所有发现均经
回归测试关闭。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# 235 passed

.\.venv\Scripts\python.exe -m ruff check .
# All checks passed!

.\.venv\Scripts\python.exe -m mypy src\trade_alpha
# Success: no issues found in 133 source files

uv pip check --python .\.venv\Scripts\python.exe
# Checked 165 packages; all installed packages are compatible
```

黄金场景在同一进程中贯通三供应商 RFQ/报价比较，并将一条由人类显式选择的路径继续送入 EP07
成本、EP08 风险、EP09 maker→reviewer→approver 和 EP10 本地草稿。审批按 `as_of` 从 append-only
决定重建，未来批准不会污染历史视图；Admin 默认无业务批准权；最终 M10 hash chain 与十阶段
predecessor 绑定均验证成功。这里的 Ready 仅代表“可供最终人工审核”，不代表已发送或已成交。

以下旧章节保留早期验收演进记录；本节和页首结论是当前基线。

## 0A. EP01–EP03 历史验收

本轮新增交易契约、RFQ 证据接入和产品规格版本化，并复用既有 M01 Artifact、M02 DocumentBlock
和 M03 Claim。SOL 负责契约、反例审查和集成；Terra/Luna 分模块实现并经历多轮缺陷回修。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# 179 passed

.\.venv\Scripts\python.exe -m ruff check src tests apps
# All checks passed!

.\.venv\Scripts\python.exe -m mypy src\trade_alpha
# Success: no issues found in 86 source files

uv pip check --python .\.venv\Scripts\python.exe
# Checked 165 packages; all installed packages are compatible
```

EP01 验证 Decimal/ISO 4217/Pint/FX/Incoterm/证据与稳定指纹；EP02 验证正文和附件独立 Artifact、
幂等来件、MIME/Office/压缩包门、原生 quarantine、可选 Docling 边界和 FE-DRY-01 候选；EP03
验证单位等价、冲突保留、范围阻断、人工决定、配置版本、PIT/diff 和授权冻结。

M01–M10 在 2026-08-24 的冻结验收基线仍为 `118 passed / 90% statement coverage`。本轮从锁文件
重建 `.venv` 后，Windows 应用控制拒绝加载未签名的 Polars runtime `.pyd`，导致 8 个旧
M04/M05 native 用例失败；失败栈均为 `Polars binary is missing` / `PyDataFrame is not defined`，
不涉及 M11 代码。未通过的环境证据保留，不把它改写成代码全绿。

## 1. 验证命令与结果

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest --cov=trade_alpha --cov-report=term-missing -q
# 118 passed; TOTAL 4253 statements, 405 missed, 90%

.\.venv\Scripts\ruff.exe check src tests apps
# All checks passed!

.\.venv\Scripts\mypy.exe src\trade_alpha
# Success: no issues found in 64 source files

.\.venv\Scripts\python.exe -m pytest tests\m11 -q
# 16 passed

.\.venv\Scripts\python.exe -m pytest -q `
  --ignore=tests\m04 `
  --ignore=tests\integration\test_storage_quality_orchestration.py `
  -k "not pandera_contract_executes"
# 120 passed, 1 deselected

uv pip check --python .\.venv\Scripts\python.exe
# Checked 161 packages; all installed packages are compatible
```

依赖已由 `uv 0.12.0` 重新解析为 `requirements-core.lock`（148 packages）和
`requirements-dev.lock`（159 packages）。

## 2. 独立模块验收台账

| 模块 | 本地 PoC 已验证 | 未宣称生产验收的边界 |
|---|---|---|
| M01 | P0 AC-001–008、012；分页、resume、A/A/B 版本、不可变 raw、quarantine、许可 hook、secret redaction | P1 真实 429 时间审计、live change watcher、目标硬件吞吐 |
| M02 | P0 AC-001–009、012；真实 Trafilatura/pdfplumber/RapidOCR、locator、schema/注入 gate、fallback、review audit | P1 live diff/cost；更大 Docling/PaddleOCR/Whisper Golden profile |
| M03 | P0 AC-001–009、012；稳定 ID、精度阈值、灰区复核、撤销、冲突、PIT、HS、PII；P1 AC-011 图复现 | P1 大规模多语言别名集；生产 Splink 与持久双时态库 |
| M04 | P0 AC-001–007、010、011；P1 AC-012；真实 Parquet/DuckDB/Polars、原子发布、损坏检测、备份恢复、Windows 并发 | P1 5,000 万行 p95 和对象存储小文件报告；真实 PostgreSQL/DVC 恢复 |
| M05 | P0 AC-001–008、010、012；P1 AC-009、011；Pandera/Frictionless/DuckDB/dbt 元数据、发布内生 PK/FK 检查 | 生产 dbt warehouse、完整数据域 contract 与长期漂移基线 |
| M06 | P0 AC-001–008、010、012；P1 AC-009、011；真实 Dagster Definitions、重试、terminal issue、checkpoint、精确 backfill、runbook | 部署级 OpenLineage、通知通道、真实 worker kill/RTO 演练 |
| M07 | P0 AC-001–010、013；P1 AC-011、012、014；registry lag、PIT、walk-forward、基线、透明评分、无 Alpha、资源上限 | 大样本统计功效、跨国家/HS group holdout；复杂模型仅在胜过基线后启用 |
| M08 | P0 AC-001–008、011、012；真实 Perspective、Streamlit 入口、全证据路径、CAS 并发、独立 raw/export grant | P1 目标硬件 p95、正式键盘/读屏 accessibility audit |
| M09 | P0 AC-001–009、012、014；P1 AC-011、013；冻结实验、预算/止损、人工 gate、并发幂等、负面结果、追加更正 | 持久 PostgreSQL migration、真实 CRM/邮件；付款/签约/订单仍禁止 |
| M10 | P0 AC-001–010、013、014；P1 AC-011、012；许可分离、RBAC、数据层 gate、hash-chain、version manifest、恶意内容、删除、SBOM | 外部 secret/vulnerability scanner、KMS/IdP、备份系统、真实法律 hold 审批 |
| M11 | P0 AC-001–012；CKAN/APIs.guru fixture、稳定 ID、去重、revision、状态机、SSRF/redirect/bytes gate、评分、M10 审批、disabled M01 草案、Golden 启用；P1 AC-014 stale | 真实 PostgreSQL registry、Dagster schedule、DCAT RDF adapter、生产网络 live crawl 与 P1 AC-013 大样本 Golden |
| EP01 | EP01-AC-001–007；不可变交易值、Decimal、ISO 4217、Pint 换算、显式 FX 证据、稳定指纹 | 扩展行业单位目录与正式 schema compatibility 流程 |
| EP02 | EP02-AC-001–010；本地来件 Artifact、正文/附件 lineage、幂等、quarantine、Docling 边界、产品字段候选 | 真实 Docling Office/MSG 黄金集、持久 intake repository、生产文件扫描服务 |
| EP03 | EP03-AC-001–011；Claim lineage、单位等价、冲突、范围门、决定、不可变版本、PIT/diff、冻结授权 | M10 真实授权适配器、事务型持久 repository、更多产品族 profile |
| EP04 | 供应商主档/场地 PIT、能力声明、筛查、资格决定与不可变快照 | 生产企业注册信息与制裁筛查后端 |
| EP05 | campaign/PIT、精确资格绑定、披露策略、安全模板/附件、NullOutbound | 真实外发仍被结构性禁止 |
| EP06 | 三格式证据接入、人工字段决定、报价快照、归一化/FX、可比矩阵 | 更大真实报价文档 Golden 集 |
| EP07 | Decimal 成本场景、unknown 非零化、补充成本决定、margin trace、lineage | 持久 cost repository 与正式税费/物流来源 |
| EP08 | typed upstream、deny-by-default findings、hard block、风险决定、M10 audit | 法律/合规结论仍需专业人工审查 |
| EP09 | 证据化 readiness、production hard gate、PIT 三角色审批、M10 RBAC/audit | 生产 IdP、事务 CAS 和持久审计后端 |
| EP10 | 人工成本/条款绑定、五状态派生、sandbox 本地草稿、十阶段 manifest | 无真实 outbound；最终发送须独立模块与评审 |

## 3. 跨模块验收

已在单一测试进程中执行：

- X-AC-001：M01 raw artifact → M02 block/claim，保留同一 artifact hash 与 locator。
- X-AC-002：M02 claim/source record → M03 entity/relationship/NetworkX snapshot。
- M04→M05→M06：published snapshot → quality/release → feature-input materialization，输入 hash 和 lineage 一致。
- X-AC-004：M07 保存分数 → M08 score→feature→claim→artifact 自动遍历，UI 不重算。
- X-AC-005：只有 paper-approved M07 plan、M08 Analyst proposal 权限、M10 Approver 和 M09 人工 gate 同时满足时实验才 approved。
- X-AC-006：M09 FAILED outcome 追加后，冻结 opportunity snapshot 与旧 UI 榜单保持不变。
- X-AC-009：M10 数据商业用途撤销通过 M01 policy hook 使 connector 调用为 0，同时阻断 M04 受限读取和 M08/M10 导出，审计仍保留。
- X-AC-012：绕过 UI 直接调用 data/experiment service，Restricted 数据和 Admin 非法审批仍被拒绝。
- X-AC-013：M11 新候选只能生成 disabled M01 record；完整 M10 review 后仍保持 disabled，只有带样本证据且所有检查通过的 Golden 才能启用，随后 M01/M10 双 gate 通过。
- X-AC-014：脱敏 RFQ 正文进入不可变 Artifact，候选保留 block locator；等价单位合并、冲突保留，人工决定后由独立 approver 冻结规格，全程无 outbound 能力。

以下属于“模块原语已验证，但尚未做真实基础设施全链演练”：

- X-AC-003：M04/M05 PIT 与 M07 leakage sentinel 已分别验证，尚未用真实多来源修订仓库完成大规模 replay。
- X-AC-007/008：partition invalidation、实体撤销和旧快照不变已验证，尚未部署跨服务 event bus 做全链重算演练。
- X-AC-010：评分与 M10 version manifest replay hash 已验证；尚未从空对象存储/PostgreSQL 重建整个 M01–M10 环境。
- X-AC-011：分页、OCR fallback、gold 原子发布和 checkpoint fixture 已验证；尚未做分布式 worker/对象存储 chaos drill。

## 4. 开源组件使用真实性

测试中实际执行：Trafilatura、pdfplumber、RapidOCR、RapidFuzz、NetworkX、PyArrow、Parquet、
Polars、DuckDB、Pandera、Frictionless、PyYAML、Dagster Definitions、NumPy、Perspective。

EP01–EP03 实际执行 Pydantic Extra Types 的 ISO 4217 校验、Pint Decimal 单位换算、M01–M03
现有实现和标准库 EML/ZIP 安全边界。Docling 保持可选后端；本轮通过惰性导入和 DocumentStream
契约替身验证，没有把未安装的真实 Office 运行伪称为已验收。

作为已声明/已锁定扩展但未伪称在核心 fixture 中执行：dlt、Scrapy、Playwright、Docling、
PaddleOCR、Splink、DVC、OpenLineage、StatsForecast、ruptures、skfolio。它们保留在明确 adapter 或
扩展依赖边界，生产启用前必须单独做 Golden、成本、许可和回放验收。

M11 实际执行：Pydantic contracts、httpx MockTransport、PyYAML seed loader、CKAN/APIs.guru adapter、
DNS/IP/allowlist URL policy、candidate revision registry，以及现有 M10 `LicenseRegistry` 和 M01
`SourceRegistry` 的跨模块 policy gate。当前 Codex 网络把公共域名解析到保留代理地址 `198.18/15`；
安全策略按设计拒绝该地址，因此没有为 live demo 放宽 SSRF 规则。

## 5. 解释限制

通过本地测试证明的是接口契约、时间/版本语义、权限纵深、失败行为和参考执行路径。它不证明：

- 任意国家、语言、文档版式和来源都达到同一精度；
- 所有开源软件、模型权重和数据在任意商业部署方式下均可使用；
- 本地内存 repository 具备 PostgreSQL 的持久性、隔离级别和灾备能力；
- 当前信号一定存在可交易或可执行 Alpha。

上线前必须为真实来源冻结代表性 Golden 集、记录硬件/吞吐/成本预算、完成数据许可审核、
部署事务存储与对象存储、接入 IdP/KMS/审计后端，并重复所有 P0 与跨模块验收。
