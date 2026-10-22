# 可信度核查：接入与运行说明

状态更新时间：2026-10-20。可信度核查已从早期单篇纵向案例扩展为 50 篇完整 AI 基准；当前批次规则为 `paper_credibility / 0.2-provisional-complete`。

## 1. 当前交付

可信度核查与五维评分相互独立，检查：

1. 论文关键主张与后文证据是否一致；
2. References 的解析、元数据匹配和覆盖率；
3. 官方代码仓库及公开复现线索。

50 篇均已有三个维度分与总体分。逐篇案例位于 `data/credibility_cases/ai_batch_v0.1/`，报告和汇总位于 `data/credibility_reports/ai_batch_v0.1/`。批次说明见[50 篇结果说明](credibility_batch_results.zh-CN.md)。

早期 `paper_001` 单主张案例、`0.1-draft` Rubric 和 `sql/031_credibility_seed_paper_001.sql` 继续保留用于开发追溯，不代表当前批次状态。

## 2. 与五维评分数据的连接

五维评分数据提供锁定版本和可回查位置：

- `paper_versions`：PDF版本、SHA-256和页数；
- `paper_sections`：章节和页码范围；
- `paper_text_chunks`：正文块；
- `paper_elements`：图、表、公式、定理及页码；
- `assessments` / `dimension_assessments`：五维质量评分。

可信度核查使用独立的 `credibility_r2` schema，并通过 `paper_version_id`、`chunk_id`、`element_id` 关联五维评分数据。`scripts/build_credibility_case_from_parsed.py` 把同一份结构化输出映射为可信度合同，不需要重新解析 PDF。

## 3. 后端数据库导入

```bash
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/001_initial_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/020_seed_all_structured_papers.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/030_credibility_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/032_credibility_seed_all_ai_batch.sql
```

`032` 是当前 50 篇的正式导入入口；不要用早期单篇 `031` 代替它。

## 4. 离线重建与验证

```bash
python3 scripts/rebuild_credibility_reports.py
python3 -m unittest discover -s tests -p 'test_credibility*.py'
make validate
```

这些操作使用已保存的证据和规则，不调用 AI，也不需要 API Key。只有重新查询 Crossref 或 GitHub 时才需要网络；查询失败不阻止离线导入。

## 5. 分数和失败状态

- 主张范围不足或材料缺失：低可验证性 3 分；
- 引用未核实：4 分；解析失败、查询失败或未查询：3 分；
- 官方代码仓库可访问：7 分；完整搜索未发现公开代码：4 分；仓库查询失败或关联不清：3 分。

引用维度逐条平均，避免一条服务故障令整篇报告为空。所有原始状态、技术错误、覆盖率与失败原因仍保留。技术失败、材料缺失或低可验证性不等于引用虚假、结果错误、造假或其他学术不端。

## 6. 后端职责

本目录已经提供结构化方式、案例、报告、Rubric、Schema和SQL。后端仍需：

1. 接收 arXiv ID／链接或 PDF；
2. 用 `scripts/structure_new_paper.py` 生成一致结构；
3. 使用后端自己的 AI 和提示词评分；
4. 用50篇基准结果校准输出；
5. 提供任务、查询和可信度报告 HTTP API；
6. 返回证据位置、覆盖率、失败原因和 provisional 标识。

数据仓库不托管模型密钥，也不提供在线服务。五维和三维结果必须分别保存、分别展示。
