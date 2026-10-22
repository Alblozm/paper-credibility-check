# PostgreSQL 数据库说明

## 当前完整交付使用哪四个文件

1. `001_initial_schema.sql`：五维评分表、外键、约束、索引和查询视图。
2. `020_seed_all_structured_papers.sql`：导入50篇结构化论文和五维评分。
3. `030_credibility_schema.sql`：创建可信度核查 `credibility_r2` 表。
4. `032_credibility_seed_all_ai_batch.sql`：导入50篇三维可信度报告。

`002_seed_paper_001.sql`、`010_seed_papers.sql` 和 `031_credibility_seed_paper_001.sql` 是单篇演示或早期兼容文件，不是当前完整数据库入口。

## 导入命令

```bash
createdb pcc_pilot
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/001_initial_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/020_seed_all_structured_papers.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/030_credibility_schema.sql
psql -v ON_ERROR_STOP=1 -d pcc_pilot -f sql/032_credibility_seed_all_ai_batch.sql
```

在 pgAdmin 中创建 `pcc_pilot` 后，用 Query Tool 按以上顺序依次载入并执行四个文件。

## 当前数据规模

| 内容 | 数量 |
|---|---:|
| 论文 / 锁定版本 | 50 / 50 |
| 页面 / 章节 | 1,348 / 1,584 |
| 正文块 / 结构化元素 | 2,198 / 1,151 |
| 五维评分 / 维度分 | 50 / 250 |
| 可信度报告 / 维度分 | 50 / 150 |
| 可信度总体分空值 | 0 |

两个种子脚本均使用事务和可重复导入策略。可信度 SQL 静态验收确认包含50个 assessment、150条维度分和单一事务。

## 常用查询

五维结果：

```sql
SELECT p.paper_id, p.title, a.overall_score
FROM papers p
JOIN paper_versions v USING (paper_id)
JOIN assessments a USING (paper_version_id)
ORDER BY p.paper_id;
```

三维与总体分：

```sql
SELECT a.paper_version_id,
       a.overall_score,
       a.overall_category,
       d.dimension_id,
       d.score AS dimension_score
FROM credibility_r2.credibility_assessments a
JOIN credibility_r2.credibility_dimension_scores d
  USING (case_id, credibility_assessment_id)
ORDER BY a.paper_version_id, d.dimension_id;
```

某篇正文块：

```sql
SELECT s.sequence_index, s.section_number, s.title,
       count(c.chunk_id) AS chunk_count
FROM paper_sections s
LEFT JOIN paper_text_chunks c USING (section_id)
WHERE s.paper_version_id = 'paper_001_v3'
GROUP BY s.section_id
ORDER BY s.sequence_index;
```

可信度核查结果是 AI 开发基准。未核实、查询失败和材料缺失保留在报告 payload 中并影响可验证性分数，但不表示学术不端。导入和读取数据不需要 API Key。
