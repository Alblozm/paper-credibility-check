# 50 篇论文 AI 可信度核查结果说明

## 结论

50 篇论文均已生成三维可信度报告，而且**主张与证据、参考文献、代码与复现线索、总体分数均无空值**。五维质量分与三维可信度分继续独立保存，不相加、不互相覆盖。

本批次使用 `paper_credibility / 0.2-provisional-complete`。它是供系统开发、提示词校准和后端联调使用的试验性 AI 基准，不是人工 Gold，也不是对作者诚信的裁决。

## 失败状态如何计分

系统先使用已经执行的外部查询结果。查询成功时按匹配结果评分；仍不能消除的失败按“可验证性”降分，同时原始状态、技术错误、覆盖率和失败原因继续保留：

| 对象 | 状态 | 试验分 |
|---|---|---:|
| 主张 | 支持 / 部分支持 / 未找到证据 / 矛盾 | 10 / 7 / 4 / 2 |
| 主张 | 不确定、范围不全或材料不足 | 3 |
| 引用 | 匹配 / 元数据差异 / 未核实 | 10 / 7 / 4 |
| 引用 | 解析失败、查询失败或未查询 | 3 |
| 代码 | 官方仓库可访问 / 完整搜索未发现公开代码 | 7 / 4 |
| 代码 | 仓库查询失败、关联不清或材料不足 | 3 |
| 代码 | 经审查的失败线索 | 3 |

引用维度使用逐条平均值，避免一条 API 故障令整篇论文变成空分。总体分仅在三个适用维度都有分时产生；本批次 50 篇均满足这一条件。

**边界：技术失败、未核实、未找到材料或低可验证性，不等于引用虚假、研究结果错误、造假或其他学术不端。** 系统没有运行论文代码，也不声称完成独立复现。

## 本批次结果

| 项目 | 结果 |
|---|---:|
| 完成案例 | 50 |
| 三个维度均有分 | 50 |
| 得到总体分数 | 50 |
| 主张维度低可验证性（3 分） | 4 |
| 代码维度 7 / 4 / 3 分 | 25 / 23 / 2 |
| 总体分范围 | 3.71–7.96 |
| 总体分平均值 | 6.61 |

References 共提取 2,771 条：514 条匹配、106 条元数据冲突、1,262 条未核实、229 条解析失败、660 条外部查询失败。原始状态没有被删除或伪装成成功；它们已经以明确、可解释的方式进入引用维度分数。

## 文件位置

- 每篇证据案例：`data/credibility_cases/ai_batch_v0.1/`
- 每篇三维报告：`data/credibility_reports/ai_batch_v0.1/`
- 汇总 JSON / CSV：上述报告目录中的 `summary.json`、`summary.csv`
- 评分规则：`rubrics/credibility_v0.1/rubric.provisional-ai-batch.json`
- 离线重建程序：`scripts/rebuild_credibility_reports.py`
- 后端整库 SQL：`sql/032_credibility_seed_all_ai_batch.sql`

## 后端导入顺序

```bash
psql -d <database> -f sql/001_initial_schema.sql
psql -d <database> -f sql/020_seed_all_structured_papers.sql
psql -d <database> -f sql/030_credibility_schema.sql
psql -d <database> -f sql/032_credibility_seed_all_ai_batch.sql
```

重新计算评分不需要 API Key：运行 `python3 scripts/rebuild_credibility_reports.py`，即可根据已保存证据离线重建 50 份报告、汇总表和 SQL。
