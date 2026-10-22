# paper_001 代码与复现线索核查记录

论文：arXiv 2307.08122v3。运行方式：

```sh
python scripts/verify_code_checks.py \
  --parsed-dir ../data/data/parsed/paper_001/v3 \
  --arxiv-id 2307.08122v3 \
  --output /tmp/paper_001-code-check.json
```

结果：

- 摘要第 1 页，`paper_001_v3_chunk_0001`，原文是 `Our code is available at: https://github.com/ tianyu139/tangent-model-composition`。
- 恢复并确认 `https://github.com/tianyu139/tangent-model-composition`；README 明确列出 arXiv 2307.08122，同时还支持另一篇论文 2307.08114。
- 检查 revision `1f282134c7d57ea20dbbfa5243909eb50d698c35` 的目录，发现实现文件，`code_availability=available`。
- 找到 issue #1 和两条评论，其中有 OWNER 角色的维护者回复。内容是模型组合问题的讨论，不是明确的复现成功／失败报告。
- 由于仓库关联两篇论文，而该线程未明确指定本篇，讨论状态为 `discussion_only`，相关性为 `undetermined`；总复现信号为 `unable_to_verify`。
- 共 1 个仓库、1 个讨论、9 个来源。没有执行代码，没有填分数，没有声称系统已复现。
