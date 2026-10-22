# JSON Schema目录

本目录保存 0.6.0 AI-final 发布所使用的机器可读取结构校验规则：

- `paper.schema.json`：论文元数据；
- `parsed_paper.schema.json`：章节、正文块和图表；
- `annotation.schema.json`：人工或AI评分；
- `evidence.schema.json`：证据及其与判断的关系；
- `benchmark_manifest.schema.json`：数据集版本和成员关系。

JSON Schema用于校验交换文件；`sql/001_initial_schema.sql`中的约束用于校验关系数据库。
