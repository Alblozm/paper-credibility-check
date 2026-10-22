# parsed目录说明

这里保存从PDF解析得到、可以映射到SQL表的结构化数据，不保存PDF本身。

每篇论文按照 `paper_id/版本号/` 分目录。目前 50 篇均已完成；下面六篇仅作为目录示例：

- `metadata.json`：论文版本、哈希、解析器和质量状态；
- `sections.json`：章节层级、顺序和页码；
- `chunks.jsonl`：正文块，一行一条JSON，保留章节和页码定位；
- `elements.json`：图、表、公式等非正文元素；
- `links.json`：论文中出现的代码、数据、项目和其他链接；
- `semantic_descriptors.json`：七类带证据的事实性语义描述；
- `parse_report.json`：解析质量门和人工抽查记录。

这些文件是可审查的中间交换格式。后端通过SQL种子或导入脚本写入PostgreSQL，前端不应直接依赖本目录的文件路径。

原PDF是事实来源，正式系统应放在私有对象存储；SQL和本目录只保存对象键、版本、哈希及可检索内容。
