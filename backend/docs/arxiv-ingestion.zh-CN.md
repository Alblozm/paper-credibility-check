# 通用 arXiv 获取与结构化入库

本文说明通用 arXiv 获取与结构化入库的实现。运行时不读取 benchmark Manifest，
也不会把未知论文映射到 `paper_001`。

## 当前流程

```text
arXiv ID / abs 链接 / PDF 链接
→ 归一化 ID，保留或解析实际版本
→ 从 arXiv 流式下载 PDF，并限制大小、页数、重定向和超时
→ 校验 PDF magic、加密状态和可读页数
→ 从 arXiv Atom API 获取标题、作者、摘要
→ 按页抽取正文，识别章节、图表/定理、URL，生成稳定 chunk_id
→ 写入 papers 和 parsed_docs
→ 使用同一 ParsedDocument 进入后续评分
```

上传 PDF 从“按页抽取正文”开始走同一路径。下载 PDF 与上传 PDF 只保存在私有目录，
数据库里的 `storage_uri` 不会通过 Assessment 接口返回。

## 本地运行

在 `.env` 中填写 `DEEPSEEK_API_KEY` 后：

```bash
make run-generic
```

仅验证获取与结构化代码时，不需要模型 Key，可直接运行自动测试：

```bash
make test
make lint
```

可配置项为 `ARXIV_CACHE_DIR`、`ARXIV_TIMEOUT_SECONDS`、`MAX_UPLOAD_MB` 和
`MAX_PAGE_COUNT`。

## 已知边界

- 当前通用解析器使用 PyPDF 的文本层，扫描版论文会因正文不足而明确失败，不做 OCR。
- 图表和定理记录用于可靠编号/页码回查，不承诺完整恢复复杂表格单元格。
- `grobid` provider 当前复用通用解析器作为可靠基线；TEI 章节增强仍是后续优化，不影响
  当前通用入库路径。
- 机器学习相关性判断与版本化 Rubric 已经接入，详见
  [`eligibility-and-rubric.zh-CN.md`](eligibility-and-rubric.zh-CN.md)。
