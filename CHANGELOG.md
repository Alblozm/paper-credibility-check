# Changelog / 变更日志

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
