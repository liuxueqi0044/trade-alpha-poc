# EP-02：RFQ 与证据接入

状态：Verified for executable PoC
上游：EP-01、M01、M02
下游：EP-03

## 1. 职责与边界

EP-02 接收上传文件、EML/MSG、粘贴文本或结构化表单，把正文和附件分别保存为 M01 不可变 Artifact，运行 M02 解析，并形成 RFQ、行项目和字段候选。

EP-02 不确认最终规格，不计算价格，不发送邮件，不披露交易对手，不执行文档内指令。

## 2. 强制规则

- 使用薄 `LocalArtifactCaptureAdapter` 复用 `EvidenceStore.put_artifact()`；不得伪造 HTTP URL 或状态码。
- 同一 Artifact 可以被不同来件引用，但每个来件必须有独立 `IntakeReceipt`。
- 所有非人工候选必须定位到 Artifact 和 DocumentBlock。
- 候选只能为 proposed、needs_review 或 rejected_schema，不能为 confirmed。
- 接入、解析和产品字段抽取分别具备稳定幂等/缓存指纹。
- MIME 以文件签名检测为准；损坏、加密、异常压缩或不可识别文件进入 quarantine。
- Docling 是可选复杂文档后端，未安装时不能影响 TXT/HTML/普通 PDF 主路径。

## 3. 首版对象和服务

- `RfqIntakeCommand`
- `IntakeReceipt`
- `ArtifactLink`
- `Rfq`
- `RfqLineItem`
- `RfqFieldCandidate`
- `CandidateExtractor` 扩展点
- `RfqRepository`
- `RfqIntakeService`

## 4. 独立验收

| ID | 优先级 | 通过条件 |
|---|---|---|
| EP02-AC-001 | P0 | 文本、PDF、图片先形成 M01 Artifact，再形成 RFQ 候选 |
| EP02-AC-002 | P0 | 正文和附件独立追溯，父子关系完整 |
| EP02-AC-003 | P0 | 重复提交不重复创建 Artifact、Receipt、RFQ revision 或 ExtractionRun |
| EP02-AC-004 | P0 | 所有机器候选包含 Artifact 和 block 定位 |
| EP02-AC-005 | P0 | 低置信 OCR、损坏和加密文件不生成确认事实 |
| EP02-AC-006 | P0 | 提示注入不能触发网络、发送、写入或批准 |
| EP02-AC-007 | P0 | 多行 RFQ 稳定拆行；不确定时显式 needs_review |
| EP02-AC-008 | P1 | Docling 缺失时主路径通过；安装后 Office/EML 黄金样本通过 |
| EP02-AC-009 | P0 | 包中不存在 outbound adapter 或外部商业动作能力 |
| EP02-AC-010 | P0 | Ruff、strict mypy、模块测试及 M01/M02 回归通过 |
