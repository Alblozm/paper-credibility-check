# 数据质量报告

本目录保存自动检查和人工抽查产生的报告，例如：

- 缺失字段；
- 无效URL；
- PDF版本不一致；
- 章节或页码解析错误；
- Evidence悬空引用；
- 两名标注者评分差异；
- AI引文不存在或定位错误；
- 数据集发布前的最终验证结果。

生成文件可以是CSV或JSON。报告应记录运行时间、检查脚本版本和被检查的数据集版本。

当前的`paper_001_trial_walkthrough.md`是第一篇论文的中文完整试跑报告，适合第一次了解项目时阅读。

`fifty_paper_completion_report.zh-CN.md` 是当前 50 篇正式库的结构化、评分、SQL 实跑和验收入口。`thirty_paper_completion_report.zh-CN.md` 与 `six_paper_completion_report.zh-CN.md` 仅保留为历史里程碑记录，其旧发布状态不再代表当前版本。
