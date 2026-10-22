# 可信度 0.2 试验计分接入

当前后端在三项核查完成后，用确定性规则计算条目分、维度分、总分和类别。
计分不调用模型，也不读取 benchmark 已有报告中的现成分数。主张模型判断、引用查询和代码查询仍由各自检查器执行。

## 使用方式

使用已有 `.env`，按真实模型和解析方式启动，例如 `make run-generic`。
调用已有 `POST /v1/credibility/assessments`，指定新规则版本：

```json
{
  "schema_version": "credibility-0.1.0-draft",
  "analysis_mode": "credibility",
  "source": {
    "type": "arxiv",
    "arxiv_id": "2307.08122v3",
    "upload_id": null
  },
  "model_id": "model_standard_v1",
  "rubric_version": "0.2-provisional-complete"
}
```

创建任务返回 202；通过 `GET /v1/credibility/assessments/{assessment_id}` 轮询。
真实模型分析和外部查询可能产生费用或访问公共服务；计分本身不增加模型调用。

省略 `rubric_version` 时默认使用 `0.2-provisional-complete`。
显式发送 `0.1-draft` 时保留旧版无分行为。请求中的 schema 版本仍是现有字段合同版本，
与 rubric 的规则版本分别维护；前端调用方必须更新其 rubric 版本类型和显示逻辑才能选择新规则。

## 返回内容

- `report.rubric_status="provisional"`、`is_provisional=true`，表示试验分，未经正式规则批准。
- `rubric_sha256` 锁定后端打包的规则文件；该文件与 `data/rubrics/credibility_v0.1/rubric.provisional-ai-batch.json` 字节一致。
- 主张和引用条目带 `item_score`、`score_rule_id`；各维度带 `score`、`score_status` 和规则 ID。
- `overall.score/category` 由后端计算，不采信模型给出的数字。
- `overall.calculation.method="equal_weight_trial_dimensions"`，同时记录纳入、排除和回退维度 ID。
- 原有 `check_status`、`applicability`、覆盖率、失败原因、证据、来源和人工复核状态保持不变。
- 试验规则允许范围不完整时给数值分；`report.scope_complete` 仍保留真实的完整性判断。
- `provisional_rubric` 提示、低分提醒及检查缺口提醒与数值同时返回。

## 计分规则

规则来自 `data/rubrics/credibility_v0.1/rubric.provisional-ai-batch.json`，
技术失败的 3 分回退来自该仓库 `credibility_r2/core.py` 的 0.2 实现。

1. 主张条目：支持 10、部分支持 7、未找到证据 4、矛盾 2、无法判断或未检查 3。
   维度取最低分；选择范围不完整或解析材料不足时再纳入一个 3 分回退。
   因而已发现的 2 分矛盾不会被回退抬高到 3。人工复核 pending 保留，
   不与 benchmark 试验批次的 AI 判读是否已执行混为一谈。
2. 引用条目：匹配 10、元数据冲突 7、未核实 4、查询或解析失败／未查 3。
   维度取已提取条目的算术平均；没有条目时为 3。保留原始技术状态和覆盖率。
3. 代码：已确认归属且可访问的实现为 7；在已检查范围未发现代码为 4；其他无法核实情况为 3。
   不适用为 null 并排除。只有关联已确认仓库、相关且具有评分资格的讨论才参与调整，
   合格失败线索为 3；当前检查器输出的未审查讨论不自动加减分。
4. 总分：三个非排除维度都有分时等权平均；只有真正不适用的维度排除。
   原适用性尚不确定的低可验证性回退项保留原状态，并在计算中显式标记。
5. 类别按原始总分的下界 0／3／6／8 映射四档；小于 6 的维度产生低分提醒。

分数衡量本轮材料与核查结果的可验证性。技术失败、材料缺失或低分不等于研究结果错误、引用虚假或学术不端。

## 2026-10-20 主张修复与计分衔接

代码发布类主张在报告组装、条目计分之前与代码核查结果对账。只有已确认归属、可访问且归属／实现证据闭合的仓库才可支持代码发布声明；权重、检查点、模型制品或数据未被代码维度核实的混合声明仍为部分支持。对账改变的是有证据支持的条目结论，条目分仍使用现有 supported 10／partially_supported 7 映射；前者记录 `P_C_SUPPORTED_BY_CODE_CHECK`，后者使用 `P_C_PARTIALLY_SUPPORTED`。原判断和关联 findings 保存在 `reconciled_from`，代码证据追加到比较证据中。

证据隔离保留其他有效主张；受影响条目变为 `unable_to_determine`，或抽取排除导致 `scope_complete=false` 时，继续触发现行 3 分 fallback。维度仍取最低值，已发现的 2 分矛盾仍优先保留。隔离旨在保留有效结论和具体错误原因，不能承诺提升分数。

原先的约定是无法判断或范围不完整时维度留空；当前 0.2 实现采用上述 fallback 映射，没有据此把草稿规则写成正式批准的规则。

## 同版本保留的校准边界

为保持交付版本的一致性，本次没有悄悄修改以下 0.2 行为：

- 代码状态按最低值汇总：可访问实现 7 分和独立成功 9 分共存时仍为 7。
- 引用缺失范围继续记录，但不虚构未提取条目或为其补入平均分母。
- 类别使用未舍入总分；例如 7.9629 仍低于 8。展示端需保留足够精度，避免显示 8.0 却给低一档类别。

这些边界的修订应单独升级规则版本。本次未改 benchmark SQL schema 或批量导入脚本。
