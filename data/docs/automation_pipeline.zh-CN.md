# 30—50 篇论文自动化流水线

这套程序不再把论文数量和评分答案写死在脚本中。论文清单是唯一入口，程序按照清单逐篇完成：

默认离线数据流程为：

`清单校验 → PDF 下载 → PDF 有效性检查 → SHA-256 → 内容去重 → 来源锁定 → 结构化 → SQL → 全库校验`

已经发布的 50 篇参考评分不会默认重算。AI 五维评分是显式的可选阶段，由 `--score` 开启；后端通常使用自己的 AI 系统和本仓库评分合同。

## 1. 清单

默认清单是 `data/manifests/pipeline_papers.json`。当前已登记并处理 50 篇；后续扩展时只需继续向 `papers` 数组添加经过来源核验的记录。

每条记录必须包含：

- `paper_id`：仓库内部永久 ID，例如 `paper_007`，不能重复；
- `version`：锁定版本，例如 `v2` 或 `openreview_2024_02_11`；
- `source_type`：如 `arxiv`、`openreview`；
- `source_url`：必须直接返回 PDF；
- `title`：论文标题；
- `tags`：分层标签或选样标签；
- `enabled`：设为 `false` 时暂不处理。

可选字段包括 `authors`、`abstract`、`language`、`venue` 和 `publication_year`。论文的录用结果不能进入 AI 评分上下文。

## 2. 默认不需要评分服务

执行 `./run.sh`、`make validate`、`make db`、`make pipeline` 或 `make resume` 都不需要 API Key。它们不会覆盖已经发布的参考评分。

只有项目维护者明确需要重新生成评分时，才准备评分服务并使用 `--score`。这不是后端导入交付库的必要步骤。

默认使用 OpenAI Responses API。运行前在终端提供环境变量：

```bash
export OPENAI_API_KEY="你的密钥"
export PCC_SCORING_MODEL="gpt-5.4-mini"
```

不要把 `.env` 或密钥提交到 GitHub。如果后端有自己的模型服务，可使用命令适配器：

```bash
export PCC_SCORER_COMMAND="python3 path/to/your_scorer.py"
python3 scripts/run_pipeline.py --scorer command --resume --score
```

这个命令从标准输入读取包含评分规则和论文块的 JSON，并在标准输出返回 `score_paper_auto.py` 文件头部规定的 JSON。

## 3. 运行

首次下载和结构化（不调用模型）：

```bash
python3 scripts/run_pipeline.py \
  --manifest data/manifests/pipeline_papers.json \
  --workers 4
```

断点续跑：

```bash
python3 scripts/run_pipeline.py \
  --manifest data/manifests/pipeline_papers.json \
  --workers 4 \
  --resume
```

处理指定论文、不调用模型：

```bash
python3 scripts/run_pipeline.py --skip-finalize --only paper_007
```

处理指定论文：

```bash
python3 scripts/run_pipeline.py --resume --only paper_007 paper_008
```

也可以使用 `make pipeline` 或 `make resume`。

## 4. 断点续跑和失败隔离

运行状态写在 `data/quality_reports/pipeline_state.json`。每篇论文、每个阶段都会记录完成时间和结果。程序同时检查清单、关键程序文件的指纹和输出文件是否存在；三者都匹配才会跳过。

一篇论文失败不会停止其他论文。最终状态会列出失败 ID；修复下载地址或异常 PDF 后运行 `--resume` 即可。只有所有选中论文成功后，程序才生成整库 SQL 和执行最终验收。

## 5. 下载、哈希与去重

下载先写入临时文件，验证 `%PDF-` 文件头且大小合理后才替换正式文件。程序使用流式 SHA-256。并行去重区带文件锁，因此两个并发任务也不能把相同 PDF 登记为两篇论文。

原始 PDF 保存到本地 `data/raw_pdfs/`，受 `.gitignore` 保护；GitHub 只保存来源记录、哈希和结构化数据。生产环境应把 PDF 放到私有对象存储。

## 6. AI 评分规则

每篇论文都使用 `rubrics/v0.1/scoring_guide.md`，生成五个等权维度：研究价值、可靠性与证据、原创性定位、清晰度与可用性、可复现性与透明度。

程序会强制检查：

- 五个维度必须完整且不重复；
- 适用维度只能给 1–10 的整数；
- 每个适用维度必须同时有支持证据和限制证据；
- 所有证据 ID 必须真实存在于该论文的结构化块中；
- 六项可复现性检查必须齐全；
- 总分由程序重新计算；
- 禁止使用外部评审和录用结果，禁止预测录用，禁止声称复现实验。

按照当前约定，已经发布的真实 AI 评分标记为 `approved` 并写入 SQL，作为本版本供后端提示词校准的固定参考结果。默认流水线只读保留它们。只有显式增加 `--score` 才调用评分器并可能更新评分。评分仍然必须保留论文内证据、运行记录和“不代表实验复现”的边界。规则模板标记为
`template_generated_not_model_scored`，不会冒充已完成 AI 评分。
每次真实模型调用的原始 JSON、模型信息和 SHA-256 保存在 `data/model_runs/`，便于审计。

## 7. 输出

- `data/source_snapshots/<paper_id>/source_record.json`：来源、版本、哈希和页数；
- `data/parsed/<paper_id>/<version>/`：章节、正文块、图表、链接和语义描述；
- `data/annotations/ai_trial/<paper_id>.json`：五维评分及证据；
- `data/quality_reports/pipeline_state.json`：执行进度和失败信息；
- `sql/020_seed_all_structured_papers.sql`：可直接导入 PostgreSQL 的全量种子文件。

结构校验默认自动执行。逐页视觉复核可通过下面的严格质量门检查：

```bash
python3 scripts/validate_full_dataset.py \
  --manifest data/manifests/pipeline_papers.json \
  --require-visual-qa
```

发布前增加 `--require-model-scoring` 可要求全部评分都有可审计的模型运行记录。当前 50 篇均通过该严格检查。
