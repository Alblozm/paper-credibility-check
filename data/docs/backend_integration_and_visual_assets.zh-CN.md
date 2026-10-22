# 后端整合、统一结构化与图表交付说明

## 1. 交付定位

本仓库交付的是一套固定的 50 篇论文参考数据库，以及与这套数据库一致的论文结构化合同。50 篇论文的 AI 分数是本项目用于校准后端评分提示词的参考答案，不要求后端重新生成。

因此，下载仓库、检查数据、导入 SQL、查询结构化正文和读取已有评分全部离线完成，不需要 `OPENAI_API_KEY`。后端选择什么模型、如何保存模型密钥、怎样重试模型请求，属于后端系统自己的实现。

## 2. 后端最短接入流程

1. 在空 PostgreSQL 数据库运行 `sql/001_initial_schema.sql`。
2. 再运行 `sql/020_seed_all_structured_papers.sql`。
3. 查询 `papers`、`paper_versions`、`paper_sections`、`paper_text_chunks` 和 `paper_elements` 获取论文结构。
4. 查询 `assessments`、`dimension_assessments` 和 `assessment_evidence_links` 获取 50 篇参考评分及证据。
5. 使用 `rubrics/v0.1/scoring_guide.md` 和 `schemas/annotation.schema.json` 约束后端自己的 AI 输出。
6. 用同一篇论文的后端评分与数据库参考评分比较，调整提示词；不要为了追平分数修改参考库。

## 3. 新论文如何使用完全相同的结构化方式

结构化程序不调用 AI，也不需要 API Key。处理上传 PDF：

```bash
python3 scripts/structure_new_paper.py \
  --paper-id paper_051 \
  --version submitted_v1 \
  --pdf /path/to/uploaded.pdf \
  --provenance-url https://example.org/papers/submitted-v1.pdf \
  --title "Paper title" \
  --authors-status missing_requires_review
```

处理公开 PDF 链接：

```bash
python3 scripts/structure_new_paper.py \
  --paper-id paper_052 \
  --version arxiv_v1 \
  --url https://arxiv.org/pdf/xxxx.xxxxx \
  --source-type arxiv \
  --title "Paper title"
```

结构化程序直接输出与参考库相同的核心解析文件：

- `metadata.json`：版本、哈希、页数和解析状态；
- `sections.json`：章节层级、顺序与页码；
- `chunks.jsonl`：按章节切分的正文；
- `elements.json`：图、表、公式和定理；
- `links.json`：代码、数据、项目等链接；
- `parse_report.json`：解析验收结果。

`semantic_descriptors.json` 是“结构化结果的 AI 语义增强层”，由后端评分模型在阅读上述核心文件后生成；它不是 PDF 版面解析器凭规则可靠判断的内容。后端完成语义提取和评分后，最终论文目录便与 50 篇参考库完全一致。字段合同见 `docs/paper_structuring_spec.md`，评分输出合同见 `schemas/annotation.schema.json`。

父章节可以没有直接正文。例如 `APPENDIX` 只作为目录父节点，其正文属于下面的 A.1、A.2 等子章节，这不是缺失数据。

## 4. 作者匿名状态

`authors=[]` 不能直接理解为数据损坏，必须同时读取 `authors_status`：

| 状态 | 含义 |
|---|---|
| `verified` | 当前版本作者已经核验 |
| `anonymous_at_assessed_version` | 被评分的是双盲评审版，PDF 当时不公开作者 |
| `missing_requires_review` | 来源没有提供作者，仍需核查 |

本库中的匿名论文已经显式标记为 `anonymous_at_assessed_version`。后端展示时建议显示“评审版本匿名”，不要显示“作者未知”。

当前明确采用该状态的 25 篇为 `paper_003` 和 `paper_007` 至 `paper_030`。这些记录对应被评分的双盲评审版本，不应为了补作者而静默改换成发表后 PDF。

## 5. 图表为什么不直接全部上传 GitHub

`paper_elements.asset_object_key` 是确定性的目标图片键，例如：

```text
renders/paper_031/iclr_2026_proceedings/page-0002.png
```

仓库不提交约千张二进制页面图片，也不提交受版权和体积限制的原始 PDF。SQL 中的视图 `paper_element_assets` 为每个元素同时提供：

- `source_pdf_url`：锁定的来源 PDF；
- `source_page_url`：可直接打开到对应页的 URL；
- `page_number`：物理 PDF 页码；
- `asset_object_key`：对象存储或本地缓存的稳定键；
- `asset_delivery_status`：当前为 `generate_on_demand` 或 `not_applicable`。

需要页面图片时执行：

```bash
python3 scripts/materialize_page_assets.py --paper-id paper_031
```

程序会下载缺失 PDF、核对仓库中锁定的 SHA-256，只渲染图表涉及的页面，并严格写入 `asset_object_key`。生产系统可把生成文件上传到 S3、MinIO 等对象存储，数据库键无需改变。

当前输出是图表所在的完整 PDF 页面，不保证裁剪到单个图表边界。现有数据可靠保存页码，但没有为全部图表人工核验矩形坐标；页面级定位更稳定，也足够用于证据核查。

## 6. 后端自己的 AI 评分流程

后端应把 `chunks.jsonl`、`elements.json` 和 `semantic_descriptors.json` 中的可定位证据组织成提示词，使用自己的模型生成符合 `schemas/annotation.schema.json` 的结果。评分必须遵守：

- 五个锁定维度及权重；
- 每个适用维度同时提供支持证据和限制证据；
- 证据 ID 必须指向结构化正文块或元素；
- 总分按适用维度重新计算；
- 不把录取决定当作论文质量答案；
- 不把“未实际复现实验”描述成“已确认可复现”。

50 篇参考分数用于提示词校准和回归测试，不是近邻相似度评分。新论文应当依据自身证据独立评分。

## 7. 离线验收

克隆仓库并安装依赖后运行：

```bash
./run.sh
```

该命令只验证已经交付的数据、模型运行记录、图表 QA 和文件关系，不下载论文、不重新结构化、不调用 AI。只有显式执行 `make score` 或 `scripts/run_pipeline.py --score` 才进入可选的重新评分路径。
