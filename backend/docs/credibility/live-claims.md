# 实时主张核查

`POST /v1/credibility/assessments` 使用所选真实模型，现场生成摘要／引言的主张，以及与后文的证据对照和判断。真实模型请求不读取 `prepared_claims`，模型失败也不会回退到预填案例。

2026-10-20 本地修复使用管线 `credibility-0.12-artifact-reconcile`、主张 Prompt `credibility-claims-live-1.5`、通用解析器 `generic-pypdf-v4`。报告组装时对代码发布类主张增加确定性跨维度对账，见下文。评分继续使用现有 `0.2-provisional-complete`，本轮不调整最低分或 3 分 fallback 规则。

## 启动与提交

保留已有 `.env`，其中需要有效的 `DEEPSEEK_API_KEY`。激活项目虚拟环境后运行：

```sh
source .venv/bin/activate
make run-generic
```

这使用真实 PDF 解析与 DeepSeek，调用会消耗供应商 API 额度。模型、思考模式、超时和重试配置均继续从现有 `.env` 读取。

```sh
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models
curl -s -X POST http://127.0.0.1:8000/v1/credibility/assessments \
  -H 'content-type: application/json' \
  -d '{
    "schema_version": "credibility-0.1.0-draft",
    "analysis_mode": "credibility",
    "source": {"type": "arxiv", "arxiv_id": "2106.09685v2", "upload_id": null},
    "model_id": "model_standard_v1",
    "rubric_version": "0.1-draft"
  }'
```

创建请求返回 `202` 和 `assessment_id`。使用返回的 ID 轮询 `GET /v1/credibility/assessments/{assessment_id}`，直到 `completed` 或 `failed`。上传 PDF 时先调用 `/v1/uploads`，然后用 `{"type":"pdf","arxiv_id":null,"upload_id":"返回的上传ID"}` 作为 `source`。

## 执行流程与验收

第一次模型调用读取解析出的完整摘要、引言，提取关键可核查主张、来源引文和条件，并去重。第二次读取现场提取的主张，以及完整可用后文、表图和附录，生成证据与判断。每阶段最多一次专用结构／证据修复；供应商错误仍使用现有退避重试。材料超出显式输入预算时明确失败，不悄悄裁掉正文。

抽取范围排除仅说明研究范围／排除项、问题动机或未来工作的陈述。上述段落中包含本文提出、证明、测量或发布的实质主张时仍须保留，包括较弱或暂时缺乏支持的主张。判断输入通过 `intentionally_excluded_blocks` 列出有意排除的 chunk ID 和角色，不包含这些块的正文；由此产生的编号跳跃不表示材料缺失。

通用解析器在已识别的 Introduction 之后，将非标准名称的顶层正文章节标为 `other`，其子章节继承该角色；References／Appendix 之后未分类的新顶层章节标为 `appendix`。嵌套章节优先继承父角色，References 的子章节继续属于 References；引言内部子章节保留引言角色，前置内容或尚未识别 Introduction 的未知章节不自动标为正文。`other` 可以作为后文证据，不再触发“章节角色未分类”的范围说明。

DeepSeek 开启思考模式时，本管线将单次输出预算设为 65,536 tokens；其他情况保留 25,000。思考开关、强度和超时继续读取已有配置。输出被供应商截断时按失败处理，不将不完整 JSON 当成报告。

主张和证据必须指向解析器产生的 chunk 或 element。页码、章节、表号从解析器回填；直接引文必须匹配原文，数字必须实际出现。判断为矛盾时，指标、数据集、基线及实验条件须明确且一致。模型不能把结果标记成人工复核通过。模型未抽取出可核查主张时明确失败，避免空结果被误当成已完成的检查。

引文匹配兼容 PDF 连字、引号、空白和单词换行；报告保存匹配到的原文片段。数值只在该原文片段中核对，不能借用同一文本块的其他数字；上标、下标及带空白的负号均有防止改义的回归用例。一次修复会收到所有已发现的主张和证据错误，且不能删除原有主张或改变主张陈述。

同阶段实际提供且角色允许的来源中，原引文只有一个严格匹配位置时，程序可纠正模型的错误锚点，并另存 `claim_anchor_correction` 审计。原引文、实验条件和判断不变；多个匹配位置、禁止角色或没有匹配时不自动猜测。纠正后仍执行全部引文、数值和角色校验。

一次修复后仍存在可隔离的证据错误时，从首次通过结构解析的原始结果恢复，逐条剔除未通过引文、角色或数值校验的 span。抽取中失去有效原文依据的主张被排除，记录原因并设 `scope_complete=false`；其余主张继续核查。判断中任何比较证据被剔除时，受影响主张保守降为 `unable_to_determine`，即使仍有其他有效引文，也不据此推断原判断成立。其他主张的结果保留。

隔离原因写入 `scope_limitations`，并保存 `model_validation_error` 快照；首次结果、修复输出和模型调用仍可追溯。修复改变主张或判断等非引文字段时不采用该修复，而是对首次结果执行隔离。重复 ID、主张／判断 ID 不闭合、原主张数字缺乏原文依据等不能安全隔离的错误仍导致阶段失败；所有主张都被排除时也不能报成功。

成功验收应检查：

- `status=completed`、`stage=done`、`progress_percent=100`，且 `paper` 对应本次输入。
- `report.versions.prompt_version` 为实时主张版本，`model_calls` 包含实际的 `claim_extraction` 和 `claim_judgment`，以及发生过的修复或重试。
- `report.checks.claims.items` 包含本次生成的主张、判断及解释；每项都有主张原文位置，有支持或矛盾判断的项还有后文证据。
- 证据 ID 闭合、引文能回查、`scope_complete` 与限制如实保留；模型结论是论文内部一致性判断。

不可用模型在入队前返回 `400`。主张模型服务失败，或修复后仍存在不能安全隔离的输出错误时，该分项为 `failed`，其他分项继续保存；可组装报告时任务可为 `completed`，但执行状态明确保留失败并允许显式恢复。可隔离错误按上述规则保留有效条目。解析失败或无法组装有效报告时任务为 `failed`。详见[持久化与恢复](durable-evidence.md)。

2026-10-20 的真实输入还暴露了 PDF 数字粘连：`speedup of13.2 times` 中的数字现在可由数字守卫识别，原引文保持不变。该回退只识别独立英文词 `of` 后的量值，保留正负号、单位和原有数值一致性检查；`proof13.2`、`ResNet50` 等名字中的数字不会因此变成量值。这不调整近似值的语义判断规则。

## 与其他可信度功能的边界

主张模型判断仍只读取论文内部材料。三项检查结束后，报告组装阶段可以用代码维度已经核实的证据对账代码发布类主张，无须增加模型调用：仓库须同时满足 `association=confirmed`、`availability=accessible`，且归属／实现证据 ID 非空、来源可读取、证据引用闭合。只关于代码的发布声明可以变为 `supported`，使用 `P_C_SUPPORTED_BY_CODE_CHECK`；同时声称发布权重、检查点、模型制品或数据的声明最多为 `partially_supported`，使用常规部分支持规则并说明未核实的部分。

只有 `unable_to_determine / not_found / partially_supported` 条目进入对账；代码结果不确定、纯数据声明、普通实验结论、否定／未来／有条件发布承诺及 `contradicted` 条目保持原判断。声明指明仓库 URL 时，须与已核实仓库匹配。代码归属与实现证据追加到 `comparison_evidence_ids`，原证据的论文／外部来源及 quote／paraphrase 类型保留。`reconciled_from` 保存原 outcome、reason、比较证据、检索范围、规则与分数、关联 findings 完整快照及本次使用的仓库 ID。单条主张对应的原 finding 调整为信息类 context，并链接新增证据；多主张共有 finding 保留其他主张的问题语义。

显式使用本页示例的 `0.1-draft` 时分数为空；要计算三维试验分与总分，改用 `0.2-provisional-complete`，见[计分接入与操作](provisional-scoring.md)。计分由后端规则完成，不复用旧五维评分，也不读取 benchmark 保存的现成评分作为当前结果。代码核查查询公开仓库与讨论，不运行论文代码。

`not_called` 只允许在 `LLM_PROVIDER=fixture` 的显式离线示例中使用。常规 DeepSeek 服务拒绝该模式；真实请求即使输入 `paper_002`，也现场生成而不是加载它的预填文件。

## 离线回归

```sh
python -m pytest -q
python -m ruff check app tests
```

自动测试使用假客户端、隔离任务状态和测试数据，不读取真实 API key 或消耗模型额度。专用测试覆盖现场生成的接口元数据、模型失败、无效证据、身份与章节角色，以及禁止预填回退。

## 真实验收记录（2026-10-19）

使用既有 `.env` 的 DeepSeek（`deepseek-flash`，保留思考模式）通过真实 POST／GET 接口运行了新输入论文 LoRA，`2106.09685v2`。该论文不在项目的 50 篇基准语料或预填案例中。任务 `cred_7d4a40ea736e48498c0ae32fe446fdb1` 在 275 秒后返回 `completed / done / 100%`。

本次四次实际调用分别完成抽取、抽取修复、判断和判断修复：现场生成 16 条主张，其中 14 条支持、1 条部分支持、1 条无法判断。46 条主张／后文引文均逐条匹配实际解析原文和页码，预填文件读取次数为零；源 PDF、`.env` 及原有本地改动的哈希核验通过。

抽查结果包括：部署合并权重后无额外推理延迟的主张有正文支持；软件包和检查点的发布声明在后文缺少确认，保留为无法判断。结果表示论文内部证据对照，解析范围限制仍保留在报告中。该阶段完整回归为 377 项通过，Ruff 和 diff 检查通过；这是较早的历史运行记录。

上述历史任务未执行 2026-10-20 新增的代码发布对账和证据隔离，原报告保留原样；本轮实现与真实复测须另列运行 ID 和结果。
