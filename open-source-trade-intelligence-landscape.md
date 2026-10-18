# 全球贸易 Alpha 基站：79 个开源项目探索与架构建议

日期：2026-08-24  
目标：寻找可以直接复用、定向改造或借鉴工程思想的开源项目，用较低成本搭建多模态、可追溯、可回测的全球贸易情报系统。

## 结论先行

这次核验了 79 个可访问的项目仓库。最重要的发现不是“存在一个现成的量化外贸项目”，而是我们可以把五个已经成熟的开源世界拼接起来：

1. 国际统计与贸易数据；
2. 调查新闻、制裁和企业实体解析；
3. 网页、PDF、图片、音频等多模态证据采集；
4. 数据版本、质量、血缘与可重复研究；
5. 量化研究中的特征、回测、组合和风险思想。

真正应该建设的不是一个“大表”，而是一个 **evidence-first intelligence system（证据优先的情报系统）**。系统既保存数值，也保存文件、网页快照、图片、地图位置、企业关系、事件、模型结论及其出处。

推荐的底层坐标因此应从原来的：

`HS × 国家 × 时间 × 贸易方向 × 伙伴`

扩展为：

`Entity × Product/HS × Place × Time/Vintage × Event × Evidence × Relationship`

其中每一条推断都必须能回到原始证据、抓取时间和解析版本。

### 优先级定义

- **A：v0.1 直接采用**——适合单机、低成本、可快速形成数据闭环。
- **B：定向验证**——有明确价值，但只在具体数据源或信号需要时引入。
- **C：工程参考/规模化后采用**——思想有价值，当前部署成本或复杂度过高。
- **R：许可或产品风险**——可以研究，但不能未经审查直接进入商业核心。

## 推荐架构

| 层 | 要解决的问题 | v0.1 推荐组合 |
|---|---|---|
| 原始证据层 | 保存 API 响应、网页、PDF、图片、音频及 hash | 文件/对象目录 + Parquet + DVC |
| 采集层 | API、静态网页、动态网页、页面变化 | dlt + Scrapy + Playwright + changedetection.io |
| 文档理解层 | 正文、表格、OCR、版面、语言 | Trafilatura + Docling + PaddleOCR；Whisper 按需 |
| 规范化层 | 国家、HS、公司、人、地点、事件统一 | FollowTheMoney + 自有贸易 schema + Splink |
| 计算层 | 清洗、特征、快照、point-in-time 查询 | Polars + Arrow/Parquet + DuckDB + dbt-duckdb |
| 质量与审计 | schema、断言、来源、版本、血缘 | Pandera + Frictionless；OpenLineage 后置 |
| 信号层 | 预测、异常、变点、组合配置 | StatsForecast + PyOD + ruptures + skfolio |
| 研究终端 | 筛选、下钻、流式表格、地图 | Streamlit + Perspective；Kepler.gl 按需 |

关键原则：同一功能只选一个主工具。不要同时部署三套编排器、三套湖表格式和三套数据目录。

---

## 一、贸易、宏观、合规与公开情报（11）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 1 | [UN Comtrade API Package](https://github.com/uncomtrade/comtradeapicall) | 官方 Comtrade Python 客户端；贸易矩阵、批量文件、双边数据和元数据获取 | **A**：第一条官方贸易主干 | MIT；数据使用条件另行遵守 |
| 2 | [wbgapi](https://github.com/tgherzog/wbgapi) | World Bank 指标、经济体、时间序列和元数据客户端 | **B**：补充 GDP、汇率环境、人口、物流等宏观暴露 | MIT |
| 3 | [sdmx1](https://github.com/khaeru/sdmx) | 统一接入使用 SDMX 的统计机构，并解析数据结构定义 | **A**：比为每个统计局重写客户端更有长期价值 | Apache-2.0 |
| 4 | [DBnomics Python client](https://git.nomics.world/dbnomics/dbnomics-python-client) | 将大量公共经济时间序列统一成 DataFrame | **B**：适合宏观和产业辅助变量 | AGPL-3.0+；网络服务和分发场景需审查 |
| 5 | [OpenSanctions](https://github.com/opensanctions/opensanctions) | 制裁、PEP、风险实体的采集、标准化、去重和导出工程 | **B/R**：代码和 schema 很值得借鉴，商业数据使用不能直接照搬 | 代码 MIT；完整数据为 CC BY-NC，商业使用需许可 |
| 6 | [FollowTheMoney](https://github.com/opensanctions/followthemoney) | 人、公司、资产、地址、付款、所有权、文件与关系的统一实体模型 | **A**：可作为企业情报/证据图的骨架，再扩展 HS、工厂和贸易事件 | MIT |
| 7 | [Aleph](https://github.com/alephdata/aleph) | 搜索海量文件和结构化数据，把人、公司与文件串成调查图谱 | **B**：最接近“文件 + 实体 + 关系 + 搜索”的开源参考实现 | MIT；整套部署偏重 |
| 8 | [Open Supply Hub](https://github.com/opensupplyhub/open-supply-hub) | 全球生产设施标识、地址清洗、去重、设施地图和贡献流程 | **B/R**：供应商主数据与 facility ID 设计很有价值 | 代码 MIT；公开数据 CC BY-SA 4.0，API/规模化商业使用有额外条款 |
| 9 | [Open Contracting Data Standard](https://github.com/open-contracting/standard) | 政府采购公告、招标、合同、供应商和履约阶段的数据标准 | **B**：公共采购可成为真实需求和买家意图信号 | 代码/标准 Apache-2.0；具体门户数据许可各异 |
| 10 | [gdeltPyR](https://github.com/linwoodc3/gdeltPyR) | 下载和处理 GDELT 事件与新闻数据的 Python 工程参考 | **B**：港口中断、禁令、罢工、灾害、补贴等事件信号 | GPL-3.0；GDELT 数据本身要求署名 |
| 11 | [world_trade_data](https://github.com/mwouts/world_trade_data) | World Bank WITS 贸易和关税 API 的 Python 封装 | **C**：直接相关但维护较慢，适合读代码和验证 WITS 查询，不宜无评估直接作为核心依赖 | MIT；WITS 数据条款另计 |

### 可复用的工程思想

- 从 FollowTheMoney 复用“实体、声明、来源、关系”而不是只复制字段。
- 从 Aleph 复用“原始文件和结构化实体同时可搜索”的模式。
- 从 OpenSanctions 复用“每条实体信息保留数据谱系、去重前后都能解释”的方式。
- 从 OCDS 复用“一个商业机会不是静态记录，而是公告—投标—授标—合同—履约的事件链”。

## 二、采集与编排（8）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 12 | [dlt](https://github.com/dlt-hub/dlt) | REST API 分页、增量游标、schema 推断、嵌套数据规范化、schema drift 和 DuckDB/文件落地 | **A**：v0.1 的主采集库 | Apache-2.0 |
| 13 | [Meltano](https://github.com/meltano/meltano) | 基于 Singer 的声明式连接器管理、环境配置和 ELT 项目结构 | **B**：当标准连接器数量明显增加时再评估 | MIT |
| 14 | [Singer Python](https://github.com/singer-io/singer-python) | Tap/Target 数据流协议和 state/bookmark 增量同步思想 | **B**：更适合作为连接器协议或遗留生态兼容层 | Apache-2.0 |
| 15 | [Dagster](https://github.com/dagster-io/dagster) | 数据资产、分区、依赖、回填、测试、元数据和可观察性 | **A**：把数据集当资产而不是把脚本当定时任务 | Apache-2.0 |
| 16 | [Prefect](https://github.com/PrefectHQ/prefect) | Python-first 工作流、重试、状态和动态流程 | **B**：如果团队更偏任务流而不是资产流，可替代 Dagster | Apache-2.0 |
| 17 | [Apache Airflow](https://github.com/apache/airflow) | 成熟 DAG 调度和庞大 provider 生态 | **C**：成熟但 v0.1 太重；不应与 Dagster 同时使用 | Apache-2.0 |
| 18 | [Kestra](https://github.com/kestra-io/kestra) | 声明式事件驱动编排、UI 和多语言任务 | **B/C**：适合未来运维团队，不是当前最低成本路径 | Apache-2.0 |
| 19 | [Apache SeaTunnel](https://github.com/apache/seatunnel) | 大规模批流一体数据集成和多引擎连接 | **C**：数据量真正进入分布式阶段后再看 | Apache-2.0 |

结论：**dlt + Dagster** 足以覆盖第一代。其余项目用于比较和未来迁移，不要一起安装。

## 三、网页、文档、图片、音视频与变化检测（12）

| # | 项目 | 数据形态与复用价值 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 20 | [Scrapy](https://github.com/scrapy/scrapy) | 大规模静态网页抓取、队列、限速、中间件、失败重试 | **A**：公开网页采集主框架 | BSD-3-Clause；仍需遵守网站条款、robots 和法律 |
| 21 | [Crawlee Python](https://github.com/apify/crawlee-python) | 爬虫队列、浏览器抓取、代理/会话和请求存储 | **B**：需要统一静态与浏览器爬取时，与 Scrapy 二选一或局部配合 | Apache-2.0 |
| 22 | [Playwright Python](https://github.com/microsoft/playwright-python) | 动态站点、登录态、下载、截图和网络请求拦截 | **A**：只用于必须执行 JavaScript 的站点 | Apache-2.0 |
| 23 | [Trafilatura](https://github.com/adbar/trafilatura) | 从新闻、公告和网页中抽取正文、时间、作者与元数据 | **A**：新闻/政策页面清洗主工具 | Apache-2.0 |
| 24 | [Docling](https://github.com/docling-project/docling) | PDF、Office、HTML、图片的版面、表格和结构化文档转换 | **A**：法规、招标、产品目录和检测报告解析主工具 | MIT；模型权重许可需分别核验 |
| 25 | [Unstructured](https://github.com/Unstructured-IO/unstructured) | 多格式文档 partition、chunk 和结构化 ETL | **B**：作为 Docling 的对照实验；不要默认两套同时运行 | Apache-2.0；部分高级能力属于商业产品 |
| 26 | [Apache Tika](https://github.com/apache/tika) | 广泛文件类型检测、文本和元数据抽取 | **B**：适合作为格式兜底层，语义版面不如专门文档模型 | Apache-2.0 |
| 27 | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 多语言 OCR、表格、版面和复杂扫描文件结构化 | **A**：中文及多语言证书、标签、装箱单、扫描法规 | Apache-2.0；模型许可分别记录 |
| 28 | [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) | 轻量、本地、成熟 OCR | **B**：清晰文本和低资源场景；复杂版面优先 PaddleOCR/Docling | Apache-2.0 |
| 29 | [Whisper](https://github.com/openai/whisper) | 多语言语音识别和翻译 | **B**：展会视频、电话/访谈、公开视频和会议记录 | MIT；音视频来源权利另计 |
| 30 | [OpenCV](https://github.com/opencv/opencv) | 图像预处理、目标/颜色/几何特征、模板比对和视频帧 | **B**：包装、标签、产品图变化及 OCR 前处理 | Apache-2.0 |
| 31 | [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | 网页内容/视觉变化监控、历史差异和通知 | **A**：监控买家产品目录、工厂证书、价格页、招聘和政策页面 | Apache-2.0 |

这里的关键不是把所有内容都送给大模型，而是先生成可审计的中间产物：`document → page/block/table/image → extracted claim → linked entity/event`。

## 四、存储、查询、数据版本与执行引擎（11）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 32 | [DuckDB](https://github.com/duckdb/duckdb) | 单机直接查询 Parquet/JSON/CSV，窗口函数、空间/HTTP 扩展 | **A**：研究和 point-in-time 查询核心 | MIT |
| 33 | [Apache Arrow](https://github.com/apache/arrow) | 跨语言列式内存格式及 Parquet 生态互操作 | **A**：数据交换底座，不需要自创内部 DataFrame 格式 | Apache-2.0 |
| 34 | [Polars](https://github.com/pola-rs/polars) | 快速、惰性、流式 DataFrame 计算 | **A**：大批量特征生成和清洗 | MIT |
| 35 | [PostgreSQL](https://github.com/postgres/postgres) | 事务、约束、索引、JSON、全文检索和服务端状态 | **A**：存元数据、任务状态、标注、用户和实时操作数据；不取代 Parquet 历史湖 | PostgreSQL License |
| 36 | [ClickHouse](https://github.com/ClickHouse/ClickHouse) | 大规模列式聚合、事件和时序分析 | **C**：只有 DuckDB 无法满足并发或数据规模时才引入 | Apache-2.0 |
| 37 | [Apache Iceberg](https://github.com/apache/iceberg) | 湖表快照、schema/partition evolution、time travel | **C**：对象存储规模化后的候选；v0.1 不需要 | Apache-2.0 |
| 38 | [Delta Lake](https://github.com/delta-io/delta) | ACID 湖表、版本、批流一体 | **C**：与 Iceberg 二选一，避免双格式 | Apache-2.0 |
| 39 | [lakeFS](https://github.com/treeverse/lakeFS) | 对对象存储做 Git-like 分支、提交、回滚和可重复实验 | **B**：当原始数据迁移到 S3 兼容存储后引入 | Apache-2.0 |
| 40 | [DVC](https://github.com/iterative/dvc) | Git 关联的数据/模型版本和可重复 pipeline | **A/B**：单机阶段版本化样本集、标注集和模型产物；不用于无限增长的全量事实库 | Apache-2.0 |
| 41 | [Trino](https://github.com/trinodb/trino) | 跨多个数据源和湖表的分布式 SQL federation | **C**：多存储、多团队后再使用 | Apache-2.0 |
| 42 | [Ibis](https://github.com/ibis-project/ibis) | 用统一 Python 表达式编译到 DuckDB、Postgres、ClickHouse 等后端 | **B**：降低未来从单机迁移的查询重写成本 | Apache-2.0 |

## 五、转换、质量、数据契约、目录与血缘（8）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 43 | [dbt Core](https://github.com/dbt-labs/dbt-core) | SQL 模型、测试、文档、依赖图和增量转换 | **A**：标准化事实表、维度表和特征表 | Apache-2.0 |
| 44 | [Great Expectations](https://github.com/great-expectations/great_expectations) | 数据质量断言、检查点和质量报告 | **B**：适合跨表/批次质量；v0.1 可先用更轻量的 Pandera | Apache-2.0 |
| 45 | [Pandera](https://github.com/unionai-oss/pandera) | Python DataFrame schema、类型、范围和业务约束 | **A**：每个采集器入口和每张特征表的轻量契约 | MIT |
| 46 | [Frictionless](https://github.com/frictionlessdata/frictionless-py) | CSV/Excel/JSON 数据包、schema、校验、资源描述符 | **A**：把一次外部数据交付包装成可验证数据包 | MIT |
| 47 | [OpenLineage](https://github.com/OpenLineage/OpenLineage) | 作业、数据集、运行事件的开放血缘标准 | **B**：先按它设计标识符，真正部署后置 | Apache-2.0 |
| 48 | [DataHub](https://github.com/datahub-project/datahub) | 数据目录、搜索、所有权、血缘、质量、业务术语 | **C**：团队和资产数量增长后有价值，当前过重 | Apache-2.0 |
| 49 | [OpenMetadata](https://github.com/open-metadata/OpenMetadata) | 目录、质量、血缘、事件、数据产品和 AI context | **C**：与 DataHub 二选一，不应成为 v0.1 前置条件 | Apache-2.0 |
| 50 | [dbt-duckdb](https://github.com/duckdb/dbt-duckdb) | 让 dbt 直接在 DuckDB 和外部 Parquet 上建模 | **A**：把本地研究站连成一条低成本 SQL 转换链 | Apache-2.0 |

## 六、实体解析、知识图谱与关系网络（6）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 51 | [Splink](https://github.com/moj-analytical-services/splink) | 概率实体匹配、阻塞、训练、解释和多 SQL 后端 | **A**：解决同一公司在海关、官网、采购、制裁和供应商表里的不同写法 | MIT |
| 52 | [dedupe](https://github.com/dedupeio/dedupe) | 主动学习式去重和 record linkage | **B**：适合有人参与标注的小规模高质量主数据 | MIT |
| 53 | [recordlinkage](https://github.com/J535D165/recordlinkage) | 经典比较、候选生成、分类与评估流程 | **B/C**：适合基线和教学，不是大规模首选 | BSD-3-Clause |
| 54 | [OpenRefine](https://github.com/OpenRefine/OpenRefine) | 交互式清洗、聚类、reconciliation 和人工纠错 | **B**：非常适合审阅国家、公司、地址、商品名称映射 | BSD-3-Clause |
| 55 | [NetworkX](https://github.com/networkx/networkx) | 供应商—买家—港口—国家—事件网络计算 | **A**：原型期图算法，无需先上图数据库 | BSD-3-Clause |
| 56 | [Apache AGE](https://github.com/apache/age) | 在 PostgreSQL 上增加图模型和 Cypher 查询 | **B**：关系查询成熟且 PostgreSQL 已成为操作库时再引入 | Apache-2.0 |

实体解析是这个系统的核心护城河之一。没有它，“同一公司不同语言名称”“母子公司”“工厂与出口商不是同一主体”等问题会让信号被重复计算或错误归因。

## 七、地理与物流空间（2）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 57 | [H3](https://github.com/uber/h3) | 全球六边形空间索引、邻域、聚合和跨尺度比较 | **B**：把工厂、港口、灾害、基础设施和运输事件映射到稳定空间单元 | Apache-2.0 |
| 58 | [PostGIS](https://github.com/postgis/postgis) | PostgreSQL 空间对象、距离、相交、路线附近和区域暴露查询 | **B**：需要空间风险和设施网络后启用 | GPL-2.0+；作为独立数据库扩展使用通常更易管理 |

## 八、量化研究、预测、异常、变点、组合与因果（13）

| # | 项目 | 可以复用或借鉴什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 59 | [Qlib](https://github.com/microsoft/qlib) | 数据集、特征、模型、策略、记录器、实验和评估的分层 | **C（强参考）**：最值得借鉴的是研究框架，不是直接套股票模型 | MIT |
| 60 | [LEAN](https://github.com/QuantConnect/Lean) | 确定性事件回放、时钟、组合状态、订单/成交和性能统计 | **C（强参考）**：可改写成贸易机会、询盘、样品、订单和资源分配事件引擎 | Apache-2.0 |
| 61 | [vectorbt](https://github.com/polakowo/vectorbt) | 向量化参数扫描和组合回测思想 | **R**：研究 API 思想即可，不建议把它作为商业核心依赖 | Apache-2.0 + Commons Clause，限制以其功能为主要价值的销售 |
| 62 | [Zipline Reloaded](https://github.com/stefan-jansen/zipline-reloaded) | point-in-time 数据、事件驱动回测、pipeline 和绩效分析 | **C**：适合研究“当时可知信息”的工程方法，金融资产假设需要拆掉 | Apache-2.0 |
| 63 | [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt) | 均值—方差、风险模型、Black-Litterman、层次风险平价 | **B**：将销售时间、样品预算和库存额度分配到机会组合 | MIT |
| 64 | [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | 多种风险度量、约束、风险预算与稳健组合 | **B**：比简单排名更适合国家/产品/客户集中度管理 | BSD-3-Clause |
| 65 | [skfolio](https://github.com/skfolio/skfolio) | sklearn 风格的组合优化、交叉验证、风险和模型选择 | **A/B**：最适合把机会评分接到可验证的资源配置流程 | BSD-3-Clause |
| 66 | [StatsForecast](https://github.com/Nixtla/statsforecast) | 大规模统计预测、间歇需求、概率区间和多序列计算 | **A**：HS×国家月度面板的第一代可解释基线 | Apache-2.0 |
| 67 | [sktime](https://github.com/sktime/sktime) | 统一时间序列任务、pipeline、评估和模型比较 | **B**：适合建立规范化研究接口，初期可能比单一库更重 | BSD-3-Clause |
| 68 | [River](https://github.com/online-ml/river) | 在线学习、概念漂移、逐条更新和流式指标 | **B**：适合新闻/网页/询盘信号持续更新，但成交标签稀疏时要克制 | BSD-3-Clause |
| 69 | [PyOD](https://github.com/yzhao062/pyod) | 多种异常检测算法和一致接口 | **A**：发现进口量、单价、供应份额、网页变化等异常 | BSD-2-Clause |
| 70 | [ruptures](https://github.com/deepcharles/ruptures) | 离线变点检测 | **A**：识别需求、价格、供应结构和政策环境的 regime shift | BSD-2-Clause |
| 71 | [DoWhy](https://github.com/py-why/dowhy) | 因果图、识别、估计、反驳和敏感性分析 | **B**：用于区分“政策发生后增长”与“政策导致增长”；需要明确因果假设 | MIT |

### 量化层最需要移植的不是交易公式

应该移植的是：

- point-in-time 数据和 vintage；
- train/validation/test 的时间隔离；
- 信号 IC、稳定性、换手成本和衰减；
- 组合暴露与集中度约束；
- 纸面验证、小仓位、逐级加仓；
- 每次决策的完整 audit trail。

不能直接移植的是：股票的高频价格、流动性、即时成交、连续可交易性和清晰 P&L 标签。

## 九、研究终端、实时表格、仪表盘与地图（8）

| # | 项目 | 可以复用什么 | 评价 | 许可/风险 |
|---:|---|---|---|---|
| 72 | [Apache Superset](https://github.com/apache/superset) | SQL BI、仪表盘、权限和图表 | **B**：面向多人消费稳定指标时采用 | Apache-2.0 |
| 73 | [Metabase](https://github.com/metabase/metabase) | 易用自助查询、问题和仪表盘 | **B/R**：内部使用方便；嵌入、分发和企业功能需仔细看双重许可 | 社区代码 AGPL，部分为商业许可 |
| 74 | [Grafana](https://github.com/grafana/grafana) | 运行监控、告警、时序和日志面板 | **B**：更适合系统健康与告警，不是首选研究终端 | AGPL-3.0 |
| 75 | [Streamlit](https://github.com/streamlit/streamlit) | 快速构建 Python 研究应用和人工审阅界面 | **A**：v0.1 最快的人机界面 | Apache-2.0 |
| 76 | [Panel](https://github.com/holoviz/panel) | 更灵活的 Python 数据应用和复杂交互 | **B**：当 Streamlit 的状态/布局限制明显时再评估 | BSD-3-Clause |
| 77 | [Perspective](https://github.com/finos/perspective) | 浏览器中的大数据透视、聚合、流式更新和高性能表格 | **A**：非常适合“贸易 Bloomberg”式矩阵和下钻 | Apache-2.0 |
| 78 | [Kepler.gl](https://github.com/keplergl/kepler.gl) | 大规模地理点线面探索和时间动画 | **B**：供应链、港口、灾害、设施和贸易流空间分析 | MIT |
| 79 | [deck.gl](https://github.com/visgl/deck.gl) | 可编程 WebGL 地理可视化图层 | **C/B**：需要自定义产品级地图时采用；Kepler 更快出原型 | MIT |

---

## 第一批真正值得拿来做 PoC 的项目

如果只允许保留一组低成本技术，我会选择：

1. **官方与开放数据**：Comtrade API、sdmx1、wbgapi；
2. **证据与实体模型**：FollowTheMoney；
3. **采集**：dlt、Scrapy、Playwright、changedetection.io；
4. **内容理解**：Trafilatura、Docling、PaddleOCR；
5. **计算与版本**：Arrow/Parquet、Polars、DuckDB、DVC；
6. **转换与契约**：dbt Core、dbt-duckdb、Pandera、Frictionless；
7. **实体链接**：Splink、NetworkX；
8. **量化研究**：StatsForecast、PyOD、ruptures、skfolio；
9. **界面**：Streamlit、Perspective。

这不是要求同时安装 25 个服务。多数是 Python 库；v0.1 仍然可以是一台机器、一个仓库、一个 DuckDB 数据库和一批 Parquet 文件。

## 建议直接借鉴的五种工程结构

### 1. OpenSanctions / FollowTheMoney：声明而不是最终真相

不要直接覆盖公司名称、地址或所有权。保存每个来源提出的 `claim`，再由匹配和置信度层形成当前视图。这样可以保留冲突、历史变化和证据。

### 2. Qlib：研究对象分层

把系统拆成 `Dataset → Feature → Model → Signal → Strategy → Record`。任何新模型都不能绕过相同的数据切分、成本假设和评估协议。

### 3. LEAN / Zipline：确定性事件回放

定义一个只读取“当时已经观察到的数据”的时钟。政策发布日期、网页首次发现时间、贸易数据发布日期和后续修订时间必须分开。

### 4. Dagster / OpenLineage：资产和血缘

每个原始快照、规范化表、特征、候选榜单和报告都是有版本的数据资产。一个错误的 HS 映射应该能回答“影响了哪些信号和决策”。

### 5. changedetection.io + Docling：非数值信号也可回测

网页、PDF 和图片先做版本差异，再从差异中提取事件。例如“产品目录新增一类 SKU”“认证证书更新”“招聘页出现采购经理”“法规附件中的生效日期改变”。不要每天重新让模型阅读整份材料而丢失变化路径。

## 不建议现在做的事情

- 不要先部署 ClickHouse + Trino + Iceberg + DataHub + Airflow 的“大数据全家桶”。
- 不要把所有网页和 PDF 只转成 embedding；embedding 不能替代原文、版本、表格和证据坐标。
- 不要让 LLM 直接写入最终事实表。它应写入带模型版本、置信度和证据位置的候选声明。
- 不要用修订后的最新数据回测历史信号。
- 不要把“代码开源”误认为“数据可以免费商业使用”。
- 不要直接套用股票回测器的收益率和成交假设。

## 推荐的第一个垂直验证

先做一条端到端链路，而不是继续扩充项目清单：

1. 从 Comtrade 取得一个有限历史切片，并保存原始响应、检索时间和 hash；
2. 从一个政策/采购网站抓取 HTML 和 PDF 快照；
3. 用 Trafilatura/Docling/PaddleOCR 抽取正文、表格和事件候选；
4. 用 FollowTheMoney 风格 schema 表示公司、地点、文件和关系；
5. 用 Splink 把同一企业的不同名称连接起来；
6. 在 DuckDB 中生成 point-in-time 特征；
7. 用 StatsForecast、PyOD、ruptures 生成预测、异常和变点信号；
8. 在 Perspective/Streamlit 中展示排名，并能点回每条原始证据。

这条链路成功后，我们才有依据决定哪些项目进入正式架构，哪些只是看起来先进。

## 许可提醒

本报告核验的是仓库顶层许可证和公开许可说明，不构成法律意见。软件许可证、模型权重、训练数据、第三方连接器、API 条款、网站服务条款和具体数据集许可必须分别记录。尤其需要优先审查：OpenSanctions 数据、Open Supply Hub API、DBnomics 客户端的 AGPL、Metabase/Grafana 的网络 copyleft，以及 vectorbt 的 Commons Clause。
