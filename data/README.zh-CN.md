# 基准数据

[English](README.md) · **中文**

这是一套小而有据可查的基准，包含 50 篇机器学习论文，用于开发、校准和回归测试 Paper Credibility Check。数据集 `iclr_pilot`，发布版本 `0.6.0-ai-final`（见 `data/manifests/release.json`）。

- **论文**：30 篇 ICLR 2024 论文组成均衡的校准集，另有 15 篇 ICLR 2026 和 5 篇 ICLR 2025 正式论文作为时间外推的留出集。
- **结构化正文**：每篇论文都解析成元数据、章节、正文块和论文元素（表格、图），每一项都能追溯到页码。
- **五维评分**：在 rubric `iclr_inspired_due_diligence` `0.1-draft` 下、只依据论文内证据的 AI 评估。
- **可信度报告**：50 篇论文都有主张与证据、参考文献、代码与复现三项分数和总分，使用试行规则 `0.2-provisional-complete`。

这些是供开发和提示词校准用的 AI 基准分数，不是人工 Gold 标注，不预测录用结果，也不代表任何实验已被复现。外部查询失败或材料缺失会降低"可验证性"得分，原始失败状态保留在记录里。

## 快速开始

需要 Python 3.9 及以上（后端需要 3.10 及以上）。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make validate        # 离线跨文件验收检查，不需要 API key
python3 -m pytest tests
```

导入 PostgreSQL：

```bash
createdb pcc_pilot
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/001_initial_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/020_seed_all_structured_papers.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/030_credibility_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/032_credibility_seed_all_ai_batch.sql
```

`make db` 会执行前两个文件（数据库名由 `PGDB` 指定，默认 `pcc_pilot`）。

## Make 命令

```bash
make validate   # 清单和全量跨文件验收（离线）
make sql        # 重新生成 sql/020_seed_all_structured_papers.sql
make db         # 把 schema 和完整种子导入 $(PGDB)
make pipeline   # 按清单下载并解析论文，保留已发布的分数（需要联网）
make resume     # 继续上次的下载与解析
make pilot      # 不调用 API，重建六篇回归样本
make score      # 真实模型评分，需要 OPENAI_API_KEY
```

`python3 scripts/rebuild_credibility_reports.py` 可以离线重建已保存的可信度报告和对应 SQL，不需要 API key。

## 目录

| 路径 | 内容 |
|---|---|
| `data/manifests/` | 论文清单、数据集成员、发布状态 |
| `data/source_snapshots/` | 每篇论文的来源版本、URL、校验值和存储策略 |
| `data/parsed/` | 通过质量门的章节、正文块和元素 |
| `data/annotations/` | AI 试评分，以及可选人工复核的模板 |
| `data/credibility_cases/` | 可信度核查的证据案例 |
| `data/credibility_reports/` | 50 篇论文的可信度报告 |
| `data/model_runs/` | 五维评分的模型运行记录 |
| `data/quality_reports/` | 解析与验收报告 |
| `credibility_r2/` | 无第三方依赖的可信度案例、报告工具和命令行 |
| `rubrics/` | 带版本的评分规则和锚点 |
| `schemas/` | 各类记录的 JSON Schema |
| `scripts/` | 采集、解析、评分、校验和 SQL 导出 |
| `sql/` | PostgreSQL schema 和种子数据 |
| `docs/` | 字段定义、结构化规范、流程和数据来源 |

建议从 [docs/data_dictionary.md](docs/data_dictionary.md)、[docs/paper_structuring_spec.md](docs/paper_structuring_spec.md) 和 [rubrics/v0.1/scoring_guide.md](rubrics/v0.1/scoring_guide.md) 读起。可信度批次的说明见 [docs/credibility_pipeline.zh-CN.md](docs/credibility_pipeline.zh-CN.md) 和 [docs/credibility_batch_results.zh-CN.md](docs/credibility_batch_results.zh-CN.md)。

## 新增一篇论文的流程

1. 选一篇公开的 ICLR/OpenReview 论文，记录选它的理由。
2. 在 `data/manifests/papers.csv` 记录稳定标识和来源 URL。
3. 锁定论文的具体版本和校验值。原始 PDF 不提交。
4. 把论文解析成元数据、章节、正文块和论文元素。
5. 按锁定的五个维度、只用论文内证据评分。
6. 校验证据链接、分数、结构和数据库约束。
7. 导出供后端使用的记录。

状态定义见 [docs/workflow.md](docs/workflow.md)。

## 隐私与存储

不要提交上传的 PDF、API key 或私人材料。公开论文的 PDF 只记录 URL、版本和 SHA-256 校验值，下载后存放在 git 之外。
