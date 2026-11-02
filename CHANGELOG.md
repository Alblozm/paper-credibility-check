# Changelog / 变更日志

## 1.1.0 — 2026-11-01

### English

- **PaperTrace**: the app now carries its product name, logo and icon.
- **New interface**: a start page with a model picker; a waiting page with per-check progress; a run page that shows the credibility and quality parts side by side, with a Paper view (the PDF with evidence highlighted in place, outline, page thumbnails, highlight layers and rearrangeable widgets) and a Report view; title bar tabs for every run; a History page with retry and delete; export as a printable PDF report or raw JSON.
- **Help assistant**: an optional service that answers questions about using PaperTrace from a maintained guide, with a static check that keeps the guide in step with the interface.
- **Models**: Claude and Gemini through their OpenAI-compatible endpoints, alongside GPT and DeepSeek.
- **Run control**: stop a single check (recorded as stopped by the user, fallback score), cancel a whole run, delete finished records, and read per-check progress while a run is in flight.
- **Same-run pairing**: a five-dimension assessment can carry `credibility_assessment_id`, so both parts of a run stay together in every client.
- **Code & reproduction**: a separate search for third-party reproduction reports, every issue collected for discussion review, batched semantic review, and a configurable source budget (default 60 reads).
- **Parsing**: titles with subtitles or mixed case are joined correctly across lines.

### 中文

- **PaperTrace**：应用启用正式产品名、logo 和图标。
- **新界面**：开始页可选择模型；等待页显示每项检查的进度；运行页把可信度和五维两部分并列，提供 Paper 视图（PDF 原文上直接高亮证据，含目录、页面缩略图、高亮图层和可重排的组件区）和 Report 视图；每次运行在标题栏有自己的标签页；History 页支持重试和删除；可导出可打印的 PDF 报告或原始 JSON。
- **帮助助手**：可选的独立服务，依据维护好的操作指南回答使用问题，并有静态检查保证指南与界面同步。
- **模型**：在 GPT、DeepSeek 之外，通过 OpenAI 兼容接口接入 Claude 和 Gemini。
- **运行控制**：可单独停止一项检查（记为用户停止并按兜底分计分）、取消整次运行、删除已结束的记录，运行中可查看分项进度。
- **同一次运行配对**：五维评估可以带 `credibility_assessment_id`，任何客户端都能把同一次运行的两部分放在一起。
- **代码与复现**：新增第三方复现报告的独立检索，讨论采集覆盖全部 issue，语义复核分批进行，来源读取上限可配置（默认 60 次）。
- **解析**：带副标题或大小写混排的标题可以正确跨行拼接。

## 1.0.0 — 2026-10-22

### English

First public release: frontend, backend and benchmark data in one repository.

- **Credibility check**: three parallel checks (claims & evidence, references, code & reproduction) with trial scores under rubric `0.2-provisional-complete` and response schema `credibility-0.5.0-draft`. Reports include coverage, an execution trace, external review and code discovery provenance.
- **References**: Crossref matching, then arXiv and official publisher lookups, then a bounded search agent. Crossref is rate-limited to one request per second across all jobs.
- **Code & reproduction**: official repository discovery, implementation sampling, and public issue and discussion review. No repository code is executed.
- **Paper scoring**: five-dimension assessment under field contract v0.4 with page-level evidence and a six-item reproducibility checklist.
- **Frontend**: credibility check as the home page, an optional quick five-dimension score while a check runs, a "How this check ran" panel, a merged Reports page with filters and search, and polling that backs off and retries after network errors.
- **Models**: OpenAI GPT and DeepSeek, selectable per job; a fixture model for key-free development.
- **Benchmark**: 50 structured ICLR papers with five-dimension scores and credibility reports, JSON Schemas, rubrics and PostgreSQL seeds.

### 中文

首个公开版本：前端、后端和基准数据合并到同一个仓库。

- **可信度核查**：三项并行检查（主张与证据、参考文献、代码与复现），按试行规则 `0.2-provisional-complete` 给出试验分，响应结构为 `credibility-0.5.0-draft`。报告包含覆盖率、执行记录、外部审阅和代码发现的来源。
- **参考文献**：先匹配 Crossref，再查 arXiv 和出版方官方页面，最后交给有预算限制的搜索 agent。所有任务共享 Crossref 每秒 1 次的限流。
- **代码与复现**：发现官方仓库、抽查实现文件、阅读公开 issue 和讨论，不运行仓库代码。
- **五维评分**：按字段合同 v0.4 评分，附页码级证据和 6 项可复现清单。
- **前端**：可信度核查作为首页；核查进行时可选做五维快评分；新增 "How this check ran" 面板；合并后的 Reports 页面支持筛选和搜索；网络中断时轮询会退避重试。
- **模型**：OpenAI GPT 和 DeepSeek 可按任务选择；另有不需要 key 的 fixture 模型用于开发。
- **基准数据**：50 篇结构化 ICLR 论文，含五维评分和可信度报告，以及 JSON Schema、Rubric 和 PostgreSQL 种子数据。
