# 单篇论文完整试跑：paper_001

> **版本：Rev 2（2026-09-02）**。Rev 1（2026-09-01）的流程结论保留；本次修订重新核验了全部证据位置、拆出证据 stance、并把解析覆盖缺陷从"仍需改进"提升为**质量门未通过项**。
> 修订原因见文末《Rev 1 → Rev 2 修改说明》。

## 本次试验的结论

我们用 arXiv `2307.08122v3` 完成了从"论文链接"到"可供后端读取的结构化记录"，再到"带证据的 AI 暂定评分"的完整最小流程。暂定总分为 **7.6/10**（五维等权，重算一致）。

它只是**流程验证结果**，不是人工 Gold 标注，不是录用预测，也**尚未通过 `approved` 数据质量门**。当前 `data_status = parsed`。

## 第 1 步：锁定论文版本

- 输入：`https://arxiv.org/abs/2307.08122`
- 实际评估版本：`v3`（2024-05-14 修订，89 KB）
- 版本历史：v1 2023-07-16 / v2 2023-07-20 / v3 2024-05-14 —— **2026-09-02 联网核验**
- DOI：`10.48550/arXiv.2307.08122`
- arXiv Comments 字段：`Published at ICLR 2024`
- PDF 页数：18
- SHA-256：`5bca1492f804f7ba440d4b5ff5bbc9d5f45afe5d4f184bd0904cdf46b8471d6f`（2026-08-29 记录，本次会话未重算 → **待复核**）
- 来源记录：`data/source_snapshots/paper_001/source_record.json`

关键不是只保存 arXiv 编号，而是保存版本号和文件哈希。

**Rev 2 新增的版本风险**：v3 修订于 2024-05-14，晚于 ICLR 2024 决议，因此 v3 很可能是 camera-ready，而 **OpenReview 上的公开 review 针对的是投稿版本，不是我们评的 v3**。DP-04 用公开 review 做校准时必须记住这一点——两者不是同一份文件。

## 第 2 步：保存原文，但不把 PDF 塞进 SQL

PDF 是原始证据和最终核对依据。当前试验保存在本地；正式系统应放入私有对象存储，SQL 的 `paper_versions.pdf_object_key` 只保存对象地址。PDF 不提交 GitHub，避免仓库膨胀及版权/许可问题。

## 第 3 步：解析与质量门

```bash
python scripts/parse_pdf_pilot.py \
  --pdf data/raw_pdfs/paper_001/2307.08122v3.pdf \
  --output data/parsed/paper_001/v3 \
  --paper-id paper_001 \
  --version v3
```

输出：`metadata.json`（→`paper_versions`）、`sections.json`（→`paper_sections`）、`chunks.jsonl`（→`paper_text_chunks`）、`elements.json`（→`paper_elements`，12 表 4 图）、`parse_report.json`。

### ⚠ Rev 2 更正：质量门**不应**记为 passed

`parse_report.json` 目前写的是 `acceptance_gate: "passed"`、`metadata.json` 写的是 `parse_quality: "good"`。复核后这两项都不成立：

| 缺陷 | 实测 | 违反 `paper_structuring_spec.md` |
|---|---|---|
| 章节无正文块 | **28 个章节中 16 个（57%）没有任何 chunk**，含 Abstract、3.2/3.4/3.5、4.1、4.3、4.4、4.6、附录 A、C.1/C.2/C.4/C.6/C.7 | §4「chunks carry page and section locations」形同虚设；验收门第 4、5 条 |
| 按页切块 | 18 页 = 18 块，块跨章节边界（chunk_008 同时含 §4.6 与 §5） | §4「do not cross section boundaries」 |
| 章节归属错 | 用"该页最后一个标题"归属，导致 Table 1→sec_3_3（实际 §4.2 讨论）、附录 A 正文→sec_b、Table 7/8→sec_c_3（实际 C.1/C.2） | §3、§5 |
| 缺 references 角色 | 第 10–12 页参考文献被并入 sec_5 Discussion，`section_role` 无 `references` | §3 角色表 |
| 缺 limitations 角色 | 局限性写在 §5（第 9 页），但无章节标 `limitations` | §3 角色表 |
| 元素质量 | 16 个元素全部 `parse_quality: partial` | 与 `parse_quality: good` 自相矛盾 |

**结论**：`parse_quality` 应改为 `partial`，`acceptance_gate` 应改为 `passed_with_conditions`，并附上上表。18 页渲染图确实已逐页目视检查、阅读顺序可用——这部分 Rev 1 的判断成立；不成立的是"结构化索引可用于证据检索"。

## 第 4 步：事实提取与评分分离

结构化记录回答"论文里有什么"，评分记录回答"这些证据说明论文质量如何"。两者不能混为一张表，否则改评分规则时会污染原始事实。

评分记录：`data/annotations/ai_trial/paper_001.json`（Rev 2 已按 Field Contract v0.4 §11/§15 补全）

| 维度 | 分数 | 置信度 | 核心理由（Rev 2） |
|---|---:|---|---|
| 研究价值 | 8 | high | 一个机制同时打通高效微调、组合、遗忘与隐私，且不靠 SOTA 论证价值；实验只覆盖视觉分类单一骨干，论文自述任务"并不穷尽"。 |
| 严谨性与证据 | 7 | medium | 推导 + 7 数据集 + 基线 + 三项消融 + 附录成本比较；**全文无方差/置信区间/重复实验/显著性检验**，且 Table 1 平均行显示 TAFT 低于 NLFT。 |
| 原创性与定位 | 8 | medium | 与线性 ResNet（表7）、Model Soup（表2/10）、Adapter/LoRA（表11）逐一做了**可测量**的区分；属组合式创新，未做穷尽查新。 |
| 清晰性与可用性 | 8 | high | 实验小节直接以问题命名，局限性在正文明列；实现细节分散在 §4.1 与附录 A + 表4–6。 |
| 可复现性与透明度 | 7 | medium | 有代码链接、7 个公开数据集、较全超参数；**全文无硬件/软件环境**、随机种子只提"固定"不给值、预处理只说"不做增强"。 |

等权总分：`(8 + 7 + 8 + 8 + 7) / 5 = 7.6`，`rating = good`（重算一致）。

**分数未变，依据变了**。Rev 2 没有调分，只是把 Rev 1 缺失的反面证据补上并纠正了错位的引用。其中 `soundness_evidence` 已标记 `disagreement_flag`：第二位标注者若更重视"零方差报告"与"平均行"，给 6 分同样有依据——这正是 DP-04 要记录的分歧，不应提前收敛。

## 第 5 步：证据如何连接到分数

Rev 2 起，证据位置（`EvidenceReference`）与证据用途（`EvidenceLink`）分表，后者带 `stance` 与非空 `reason`（合同 §15.4）。当前 **11 条证据 / 21 条关联（13 支持 / 8 削弱）**。

三处 Rev 1 的证据问题已修正：

1. **页码错误**：Rev 1 把"表 4–6"整体标在第 13 页，实际 **Table 6 印在第 14 页** → 拆为 ev_005（p13）/ ev_005b（p14）。
2. **章节名虚构**：Rev 1 写的 `Method and Parallel Training`、`Composition and Differential Privacy` **不是论文中印出的标题** → 改为解析归属 + 实际讨论位置两栏并列。
3. **同证据结论互斥**：Rev 1 用同一段附录既证明"披露了增强策略"（支持可复现性）又断定"预处理缺失"。核对原文后确认论文原句是 `We do not use any data augmentation for our experiments.` → 统一为 `not_available`，并新增 ev_008 承载该原句。

同时新增两类此前缺失的证据：

- **反面证据 ev_009**：§3.3 原文 "this method of unlearning through shard removal is not scalable" —— Rev 1 把"遗忘"整体记为优点，未记论文自己的可扩展性让步。
- **缺席证据 ev_010**：对全部 18 个 chunk 做关键词扫描（variance / std / confidence interval / significance / 重复实验），**零命中**。这是"论文没有做什么"的证据，`page_number` 为 `null`。注意其 `locator_type: document` 与 `evidence_mode: absence_check` 是**对合同 §15 的提议扩展，待 PM 确认**；且该结论只覆盖已生成的 18 个块，不覆盖 16 个空章节，人工须对 PDF 复核后才能定稿。

## 第 6 步：如何进入 SQL

导入顺序：`papers` → `paper_versions` → `paper_sections` → `paper_text_chunks` / `paper_elements` → `model_runs` → `assessments` / `dimension_assessments` → `evidence_references` / `evidence_links` / `reproducibility_checks`。

先保留 JSON/JSONL 便于人工审查；字段已按 SQL 表设计，验证通过后用导入脚本批量写 PostgreSQL。

## 仍需改进的地方（Rev 2 更新）

按阻塞程度排序：

1. **[阻塞 approved]** `model_id` / `model_version` / `prompt_version` 未记录。Rev 1 的 8/7/8/8/7 出自哪个模型、哪版 prompt 已无法追溯，只能记为 `PENDING_VERIFICATION`（合同 §7.3）。**事后无法补救，下一轮试跑必须在调用时写入。**
2. **[阻塞 approved]** 解析器应改为按段落 + 章节切块（目标 500–1,000 token），并保证每个章节至少有一个 chunk。当前 16 个空章节使证据检索不可用。
3. **[阻塞 approved]** DP-04 尚未完成：需赵龙翔独立试评 + 分歧记录 + 仲裁。
4. 章节/元素归属改为按 caption 与正文引用判定，不按"页内最后一个标题"。
5. 补 `references` 与 `limitations` 两个 `section_role`。
6. 图表单元格重建仍未完成；MVP 阶段用 caption + 页码 + 页面图作为证据是可接受的，但元素 `parse_quality` 必须诚实保持 `partial`。
7. SHA-256 需重算一次以确认与 2026-08-29 记录一致。

---

## Rev 1 → Rev 2 修改说明

| # | 修改 | 原因 |
|---|---|---|
| 1 | 质量门由 `passed` 降为 `passed_with_conditions`，`parse_quality` 由 `good` 降为 `partial` | 16/28 章节无正文块，与 spec 验收门第 4、5 条冲突；元素全部 `partial` 却报 `good` 属内部矛盾 |
| 2 | 拆分 `EvidenceReference` / `EvidenceLink`，补 `stance` + `reason` | 合同 §15.4 硬性要求；Rev 1 的 `supports_dimensions` 无法表达"同一证据削弱某维度" |
| 3 | 修正 Table 6 页码（13→14），拆分 ev_005 | 与 `elements.json` 实测页码不符 |
| 4 | 删除虚构章节名，改标解析归属 + 实际讨论位置 | Rev 1 的两个 `section_title` 在论文中不存在，属证据错配 |
| 5 | 统一预处理判定为 `not_available`，新增 ev_008 | 消除 Rev 1"同证据得出互斥结论"的缺陷 |
| 6 | 新增 ev_009（遗忘不可扩展）、ev_010（无方差报告） | Rev 1 只记优点不记论文自述让步；缺席证据此前完全没有载体 |
| 7 | 补 `claimed_contributions` / `claim_checks` / `strengths` / `concerns` / `unresolved_questions` / `overall_assessment` / `limitations` | 合同 §11 要求，前端依赖完整报告样例 |
| 8 | `schema_version` 由 `0.1` 改为 `0.4` | 与 `dataset.yaml` 及合同版本对齐 |
| 9 | 新增 `data_quality_gate.gate_status = not_approved` 并列出 4 项阻塞条件 | 明确本记录**不可**用作 Gold 标注 |
| 10 | 分数一律未调整 | 本轮修的是证据与字段，不是判断。调分需要理由，`soundness_evidence` 的争议以 `disagreement_flag` 记录，留给 DP-04 裁决 |
