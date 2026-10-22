# 机器学习论文门控与版本化 Rubric

## 门控

门控位于通用结构化入库之后、五维评分之前。它采用固定状态机，不使用开放式 Agent：

1. 代码检查正文长度、解析质量、语言和最小论文结构；
2. 对合格文档执行一次严格 JSON 的机器学习相关性分类；
3. 仅当结果为 `uncertain` 或分类输出不合法时，从当前论文中按固定关键词选择证据块，
   再复核一次；
4. 只有 `eligible` 进入评分，其余结果统一返回 `failed + UNABLE_TO_ASSESS + report:null`。

分类器不浏览网络，不使用论文外知识，也不服从论文正文中的指令。跨学科机器学习论文允许
进入；arXiv category 只是信号，不是运行时白名单。

门控版本分别记录为：

- policy：`ml-paper-gate-v0.1`
- prompt：`eligibility-v0.1`

## Rubric

后端默认加载随应用发布的 `app/rubrics/v0.1/`，也可通过 `RUBRIC_DIR` 指向 `../data/rubrics/v0.1`。
启动时会验证：

- rubric ID 与版本；
- 五个维度及顺序；
- 权重总和；
- 1–10 分数范围；
- applicability/confidence 枚举；
- scoring guide 是否存在且非空。

Rubric 定义和评分指南共同计算 SHA-256。分析 Prompt 记录并包含实际 ID、版本和哈希；
模型返回的维度权重会被后端按照当前 Rubric 覆盖，然后再重算总分。

当前分析 Prompt 版本为 `analysis-v0.3`。报告 JSON 结构、证据约束和产品边界仍由后端维护，
具体评分规则从版本化 Rubric 加载。

## 验证

```bash
make test
make lint
```

回归范围包括：硬闸门、非 ML 拒绝、uncertain 二次复核、虚假 chunk 拦截、Rubric 缺失/
错误版本/错误权重，以及模型权重被版本化 Rubric 覆盖。
