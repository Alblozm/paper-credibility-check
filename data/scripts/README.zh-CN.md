# 脚本说明

## 50 篇自动化入口

- `run_pipeline.py`：清单驱动入口，默认只下载和结构化；`--score` 才调用可选评分器；
- `structure_new_paper.py`：后端处理单篇 URL 或上传 PDF 的无 API Key 结构化入口；
- `materialize_page_assets.py`：核验 PDF 哈希并按图表页码生成页面图片；
- `prepare_paper.py`：下载 PDF、有效性检查、哈希、并发去重和来源锁定；
- `score_paper_auto.py`：按最新五维规则调用 AI、绑定证据并重算总分；
- `pipeline_common.py`：清单校验、原子写入和下载等共享函数。

详细操作见 `docs/automation_pipeline.zh-CN.md`。原来的 `run_six_paper_pipeline.py`
保留为六篇历史回归入口，不再作为扩展到 50 篇的生产入口。

## 离线交付验证

仓库根目录运行 `./run.sh`。它只验证发布数据，不重新下载、不重新评分，也不需要 API Key。

## 六篇历史回归

`run_six_paper_pipeline.py` 是保留的历史回归入口，顺序执行：

```text
锁定 PDF → 结构化六篇 → 重建历史 AI 评分 → 同步来源记录
→ 重建清单 → 导出完整 SQL → 清单校验 → 全量关系校验
```

运行：

```bash
python3 scripts/run_six_paper_pipeline.py
```

## 各脚本职责

| 脚本 | 职责 |
|---|---|
| `parse_pdf_pilot.py` | 解析章节、正文块、图表/定理、URL 和质量报告 |
| `generate_ai_assessments.py` | 按 v0.1 五维标准重建有证据链接的六篇历史评分 |
| `sync_parsed_provenance.py` | 把真实页数、哈希和解析状态同步回来源记录 |
| `build_manifest.py` | 生成统一论文清单和数据集版本信息 |
| `export_paper_to_sql.py` | 导出单篇完整 PostgreSQL 种子 |
| `export_all_structured_sql.py` | 合并清单内全部论文为 `020_seed_all_structured_papers.sql` |
| `validate_manifest.py` | 检查来源、清单和枚举字段 |
| `validate_full_dataset.py` | 检查全库章节、分块、元素、语义、证据和 SQL 一致性 |
| `record_visual_qa.py` | 在全页联系表确已人工查看后记录可视化验收 |
| `sync_pipeline_state_from_outputs.py` | 用已验收输出修正断点状态和清除历史错误 |

## 解析验收重点

- 每篇必须有来源记录、精确 PDF 哈希和页数；
- 章节 ID、正文块 ID、元素 ID、链接 ID 不得重复；
- 正文块不能跨越章节，页码必须位于章节范围；
- arXiv 竖排边缘编号必须从正文中清除；
- 七类语义描述都要有正文证据；
- 每个评分维度同时需要支持证据和限制性证据；
- 六项可复现透明度检查必须完整；
- 视觉验收必须覆盖 PDF 的每一页；
- 总分必须等于五个适用维度的加权结果。

脚本不得保存账号密码。公开评审和录取决定只用于后续校准，不进入 AI 评分上下文。
