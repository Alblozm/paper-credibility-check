# 外部证据的模型辅助核查

当前实现把外部证据读取、语义判断和计分分开：固定检查器先定位论文关联的项目页、GitHub 仓库和公开讨论；缺少可确认实现时可先做有界自动发现，再对候选及讨论进行语义核查；最终分数继续由后端 0.2 规则计算。自动发现 0.5 的当前说明见本文追加节和[自动外部代码发现](code-discovery.md)。

代码入口：

- `app/credibility/execution.py::check_code_with_model`：配置、共享请求预算、运行状态与审计存档。
- `app/credibility/code_model.py::enrich_code_checks`：目标选择、模型轮次、语义结果校验。
- `app/credibility/external_tools.py::ExternalToolSession`：受控只读工具、参数检查、文本预算与证据登记。
- `app/llm/deepseek.py::DeepSeekClient.complete_tools`：真实 Chat Completions 工具协议。
- `scripts/probe_credibility_tools.py`：显式运行的供应商能力探测。

## 何时调用模型

以下情况进入辅助核查：

1. 仓库关联仍为 `uncertain`。
2. 已发现的复现讨论与论文的相关性不明，解决状态为 `unknown/disputed`，或同时包含成功和失败反馈。

每次最多选择 4 个不确定仓库、8 个讨论；优先处理失败反馈。没有这些疑点时返回 `not_needed`，不增加模型调用。尚未处理的讨论保持原状态，并记录覆盖缺口。

这个环节解释已发现的证据，不开展无限搜索。当前仍以公开 GitHub 和论文中已发现的项目页为范围；不执行仓库代码、安装依赖或训练模型。

## 两种显式模式

| 配置值 | 操作方式 | 调用记录 |
| --- | --- | --- |
| `prefetch` | 后端按缺口读取 README、已有讨论快照及必要评论，再用 `complete_json` 请模型解释这些文本。 | 标记为预取证据，不伪装成供应商工具调用。 |
| `native_tools` | 模型返回函数请求；后端校验和实际执行；以原始 `tool_call_id` 回传结果，模型再输出结论。 | 记录每轮模型调用、真实工具调用和对应输入快照。 |

模式由部署配置决定，不在任务中自动探测或静默切换。当前 DeepSeek 支持 `native_tools`；现有 OpenAI Responses 客户端使用 `prefetch`，其原生工具适配尚未实现。给不支持的客户端显式设置 `native_tools` 会返回 `unsupported`，保留原检查结果。

DeepSeek 思考模式下，后续请求会按供应商协议回传原有 `reasoning_content`。这个字段只参与内部协议，不保存到证据快照、调用审计或用户报告。工具消息使用对应的原始调用 ID，不能拿预取文本构造一次不存在的原生调用。

## 配置与启动

使用原有 `.env` 的模型凭据。以下配置可用环境变量覆盖，不需要修改或打印密钥：

| 环境变量 | 当前默认值 | 作用 |
| --- | --- | --- |
| `GITHUB_TOKEN` | 空，匿名访问 | 可选 GitHub API 令牌，仅用于通过公开地址检查的 `https://api.github.com` GET；不写入配置序列化、任务指纹、模型输入、日志或证据快照。 |
| `CREDIBILITY_CODE_MODEL_ENABLED` | `true` | 启用存在疑点时的模型辅助核查。 |
| `CREDIBILITY_CODE_TOOL_MODE` | `prefetch` | 显式选择 `prefetch` 或 `native_tools`。 |
| `CREDIBILITY_CODE_MAX_ROUNDS` | `3` | 原生模式最多模型轮次，范围 1–6；最后一轮要求直接给出结论。 |
| `CREDIBILITY_CODE_MAX_TOOL_CALLS` | `6` | 补抓工具的调用上限，范围 1–20，还受固定检查与补抓共同的 40 次外部请求预算限制。 |
| `CREDIBILITY_CODE_MAX_CHARACTERS` | `60000` | 本次外部文本总字符预算，范围 1000–150000；单来源最多向模型提供 15000 字符。 |
| `CREDIBILITY_CODE_MAX_OUTPUT_TOKENS` | `16000` | 单次模型输出上限，范围 1000–16000；思考模式的思考 token 也可能占用输出预算。 |
| `CREDIBILITY_ARTIFACT_DIR` | 上传目录同级 `credibility-artifacts` | 不可变证据与模型输入的存储位置。 |

匿名 GitHub API 可能受到较低请求额度限制。需要时由运行者在启动进程前临时注入 `GITHUB_TOKEN`；应用不会自动读取 `gh` 登录凭据，也无需把令牌写入 `.env`。令牌只发送给通过公开地址检查的 GitHub 仓库元数据请求，以及本进程已从元数据确认 `private=false` 的仓库子路径；未知仓库的直接文件读取及 `/user` 等其他 API 保持匿名，不增加隐藏请求。元数据返回 `private=true` 时明确拒绝并不保存私有正文。

GitHub 转移仓库常把 `/repos/旧属主/名称` 跳转到同源 `/repositories/数字ID`。只有当前已授权的元数据／公开仓库请求收到这种同操作路径跳转，才在本次链中继续认证；直接提交数字 ID、变换成其他操作、`/user` 或跨域跳转均不会获得该权限。公开元数据成功后保留原 alias 与合法 `full_name` 的公开证明。匿名与认证 API 使用分别的冷却和失败缓存，匿名 60 次额度耗尽不会阻止仍有额度的认证查询。

每次跳转重新检查目标并重建请求头，网页、raw 内容和其他域名均不携带 GitHub Authorization；未配置时保持匿名。认证失败和限流分别保留为 `access_denied / rate_limited`，不会变成“无代码”或“没有讨论”。

原生模式启动示例，沿用项目现有 `.env`：

```bash
LLM_PROVIDER=deepseek \
PARSER_PROVIDER=generic \
CREDIBILITY_CODE_MODEL_ENABLED=true \
CREDIBILITY_CODE_TOOL_MODE=native_tools \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18078
```

评估仍调用现有接口，不需要另一个“工具分析”接口：

```http
POST /v1/credibility/assessments
Content-Type: application/json

{
  "schema_version": "credibility-0.2.0-draft",
  "analysis_mode": "credibility",
  "source": {
    "type": "arxiv",
    "arxiv_id": "2106.09685v2",
    "upload_id": null
  },
  "model_id": "model_standard_v1",
  "rubric_version": "0.2-provisional-complete"
}
```

创建返回任务 ID 后，通过 `GET /v1/credibility/assessments/{assessment_id}` 查看进度和报告。切换模式、模型或预算后应创建新评估来比较行为；已有证据重放与检查点恢复另见持久化运行说明。

这里使用 API 合同 `credibility-0.2.0-draft`，其 HTTP 报告包含新增的 `coverage`、`execution` 和 `external_review`。显式发送旧 `credibility-0.1.0-draft` 的调用方继续收到旧报告形状，这三个字段不序列化到旧版 HTTP 响应；如需在前端展示工具调用状态和覆盖解释，应选择 0.2 合同并更新对应类型。

API 的 `schema_version` 与计分的 `rubric_version` 分别管理。选择 0.2 合同不会改变评分口径；上例仍明确采用 `0.2-provisional-complete` 试验规则，三项分数和总分继续由同一规则计算。

## 显式探测真实工具协议

能力探测独立于应用启动和评估流程。它会产生真实模型调用和一次公开页面读取，需要运行者显式传入 `--execute`：

```bash
.venv/bin/python scripts/probe_credibility_tools.py \
  --execute \
  --output /tmp/deepseek-tool-probe.json
```

探测默认读取 `microsoft/LoRA` 的公开 README。后端只在实际执行工具时生成一次性 token 并放进工具结果；模型必须在随后回答中返回这个 token。输出中的 `status="passed"` 和 `token_round_trip_verified=true` 表示真实工具请求、工具执行、同 ID 回传、后续模型读取的闭环已经通过。

探测只证明所配置供应商/模型的工具协议可用。具体论文的仓库关联、讨论解释和引用校验仍由后续论文验收确认，不能把一次探测当作跨论文准确率。

## 工具范围与证据内容

| 工具 | 参数 | 实际读取与限制 |
| --- | --- | --- |
| `read_repository_readme` | 已登记 `repository_id` | 获取并解码 README 内容；已有固定版本时使用该版本。 |
| `resolve_repository_revision` | 已登记 `repository_id` | 解析仓库默认分支的实际提交 SHA，为后续文本文件读取固定版本。 |
| `read_repository_file` | 已登记 `repository_id`、相对 `path` | 只读取已固定 SHA 下的必要文本文件；不执行内容。未固定版本、目录穿越、二进制和凭据类路径会被拒绝。 |
| `read_issue` | 已登记 `repository_id`、正整数 `number` | 获取单个公开 issue 的标题、正文和公开元数据。 |
| `read_issue_comments` | 已登记 `repository_id`、正整数 `number` | 获取该 issue 首批最多 30 条评论；有后续分页时保留未覆盖提示。 |
| `read_public_page` | 论文关联页的已登记 `source_id` | 读取已发现的项目/补充页，优先使用有效快照；不能传入任意 URL。 |

仓库只能从固定检查已登记的 GitHub 候选中选择。模型不能指定新的 owner/repo、任意主机、凭据、shell 命令或写操作。公开 GET 继续复用 `PublicCodeLookup` 的超时、缓存、限流和公开地址检查。

现有 issue/comment 的 `snapshot_ref` 可以直接读取当次完整响应，不必再次请求网页。对于列表快照，按来源 URL 提取对应 issue/comment 的真实标题和正文。提供给模型的是实际文本内容，包含换行和原始引文；不只是链接。

每次补抓先保存来源快照，再向模型提供内容。超出单来源或总文本预算的截断会显式记录；完整响应仍保存在快照中。快照丢失或哈希不匹配时，不拿旧摘录代替这次观察。

## 结果校验与状态

模型输出只能覆盖本次指定的仓库和讨论 ID。每段引用都要满足：

1. `source_id` 已存在，且对应读取成功的来源。
2. 引文是本次实际提供文本中的原样子串。
3. 仓库结论引用属于该仓库自己的来源；确认关联还需引用该论文的标题或 arXiv 标识。
4. 讨论结论的引用属于同一仓库、同一 issue 的正文或评论；其他线程和 README 不能用于证明该讨论已解决。

论文身份通过 `paper_from_context` 取得，与报告所用的 benchmark 元数据或现场解析身份一致；不能只读取可能没有标题的解析对象。输入中的 `review_target_ids` 明确指定待判断对象，工具允许读取的 registry 不等于新增判断目标。当前 Prompt 为 `credibility-external-code-1.10`；每个语义阶段最多选择 4 个待关联仓库和 8 条讨论。讨论按真实原文中的复现困难投诉、其他运行／训练／下载／加载困难、其余目标排队，同优先级保持原采集顺序。该优先级不等于相关性或失败判定；未入选讨论不冒充已经模型复核。第二阶段仅处理确认后补查新增的目标，仍使用共享剩余预算。

每个目标还单独提供 `allowed_citation_source_ids`。讨论只允许引用同仓库、同线程的 Issue／评论快照；README 用于仓库判断。工具新读取同线程内容后动态更新许可列表，不能引用其他线程或用 README 推断作者身份。

引文匹配仅容许换行、CRLF 和 Unicode 空白的展示差异，并把匹配映射回来源中的真实原文片段后保存。例如模型写成 `contains the weights`，原文为 `contains the\r\nweights`，报告保存后者。字词、大小写、数字、标点和连字符不同仍不能通过这项匹配。

未知字段、伪造来源、改写引文、漏掉指定对象、跨仓库或跨讨论引用都会使本次语义输出校验失败。失败时保留原有判断，新增的真实读取记录仍保存，方便排查。

JSON/schema、目标选择、引文格式或数字条件未获来源支持时，若本次 `max_rounds` 尚有余量，允许一次约束修复；该调用计入同一个模型轮次预算，禁止调用工具。原生模式会保留必要的供应商协议历史，修复输入与审计仍排除内部思考字段。数字条件修复须保留来源中的架构／版本／代码标识原拼写（例如 `resnet18`），删除无依据数值或保留未知；最终仍执行相同的强数字条件、引文和范围校验，不通过宽松匹配取得通过。三轮已经用完时直接返回失败，不额外增加第四轮。跨仓库/线程引用、伪造或不可用来源、擅增评分/独立性字段等越权输出直接拒收，不通过修复获得通过。

审计中的 `validation_issue` 或 `validation_issues` 给出短原因代码，例如 `SEMANTIC_QUOTE_NOT_FOUND`、`SEMANTIC_TARGET_MISMATCH`、`SEMANTIC_THREAD_SCOPE_MISMATCH`、`SEMANTIC_SCHEMA_INVALID`；按需带目标 ID、来源 ID 或 schema 字段路径，不回显原始供应商错误、凭据或模型隐藏推理。成功修复记录 `repaired=true`，原始失败输出也有独立快照，不能覆盖成从未失败。

`report.external_review` 中保留模式、状态与审计引用；完整审计包含目标 ID、输入快照、轮次、用量、供应商响应 ID（供应商返回时）、工具调用 ID 与结果。报告版本信息包含实际执行的外部模型调用。

请求发送前保存 attempt 快照，返回或失败后追加终态快照。`attempt_count` 记录客户端请求尝试，`model_call_count` 仅记录成功返回；超时或失败的用量为 null，不能按零成本处理。内部供应商网络重试仍受现有重试上限约束。

| 状态 | 含义与处理 |
| --- | --- |
| `not_needed` | 没有符合条件的疑点，未增加模型调用。 |
| `completed` | 本次选定对象的语义输出通过校验；整体代码搜索仍可能范围不完整。 |
| `unsupported` | 当前供应商客户端没有所选原生工具能力；原结果保留，分项维持部分完成。 |
| `failed` | 模型请求、轮次上限、输出或证据校验失败；原结果保留，具体缺口在审计中。 |
| `partial` | 外层执行器发现共享请求预算已用尽等条件，无法完成补查；不据此推断论文有问题。 |

工具的未知名称、错误参数、重复调用 ID、预算耗尽和禁止路径作为结构化错误回传。相同参数的重复读取可复用本次已获取的观察，不重复发起请求。超时、限流和 404 保留各自状态，不能转成“未发现问题”。

## 对评分的影响

- 模型不输出数值分，也不能修改评分规则。
- 新确认关联的仓库，必须另有可访问实现的证据，才能满足 0.2 代码可用性的 7 分条件；仅模型说“是官方仓库”不能自动获得 7 分。
- 所有辅助解释的公开复现讨论继续 `scoring_eligible=false`。模型不能把账号升级为独立复现者，不能从仓库维护者身份推断论文作者，也不能随意新增作者回复归属。
- 成功或失败都是公开用户报告，不等于本系统已经执行复现。关闭 issue 也不自动等于问题解决。
- 试验分、核查发现和覆盖缺口一起保留。详见 [0.2 试验计分说明](provisional-scoring.md)。

## 离线回归与真实验收

```bash
.venv/bin/pytest -q tests/test_credibility_external_tools.py tests/test_llm.py
```

专项覆盖真实协议形状的 MockTransport、同调用 ID 回传、内部思考字段不落盘、未知工具/路径禁止、请求和文本预算、仓库与线程交叉引用拒绝、篡改快照、模型伪造评分或独立性、预取模式和原生模式不静默互换。

论文验收需另查：是否命中正确仓库、是否实际读取正文/评论、疑点是否保留为未知、结论是否能逐段定位到快照，以及模型用量和总耗时。独立仓库、共享仓库、无代码、项目页跳转和互相冲突的复现反馈应分别覆盖。

候选中的 GeoFAR PDF 大于默认 30 MB 上传上限；本地验收服务可显式设置 `MAX_UPLOAD_MB=64`。原始固定版本获取失败时保留错误；其他公开版本必须用单独样本 ID 和 PDF 哈希留档。

供应商协议依据：[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。


## 自动发现 0.5：当前实现与 1.3 真实验收

默认请求版本现为 `credibility-0.5.0-draft`，新增报告顶层 `code_discovery`；显式 0.1／0.2／0.3／0.4 继续投影为原公开结构，原 `checks.code`、仓库、讨论及来源字段形状不因发现阶段改变。原六篇 2.3／Prompt 1.7 验收与失败历史保留在 [code-usability.md](code-usability.md)，不能充当本次自动发现版本验收。

当前实现身份为 `code-checks-2.4-discovery-intake`、仓库语义 Prompt `credibility-external-code-1.10`、发现器 `code-discovery-1.3`／发现 Prompt `credibility-code-discovery-1.3`、供应商适配器 `deepseek-code-discovery-1.1`，pipeline 为 `credibility-0.8-code-discovery`。详细流程、配置与新增验收记录由 [code-discovery.md](code-discovery.md) 管理。

模型开启、DeepSeek Search／Fetch 凭据及原生工具能力可用，且原有材料未确认可用实现／明确不适用时，才运行发现。模型只选择服务端根据当前论文题名／arXiv ID 登记的 query_id，以及搜索返回或实际已取页面链接登记的 candidate_id；不能提交任意查询、URL、owner/repo 或代码命令。搜索片段只登记线索，候选分类引用必须来自实际 Fetch 正文，题名／标识与实现归属须有局部文字关系；阅读列表、仅引用本文的比较基准和明确第三方复现分别保存。

发现阶段默认最多 3 次查询、4 次页面读取、20 个候选、4 轮模型请求和 8 次工具调用。之后原仓库／讨论语义阶段仍共享原 3 轮模型、6 次工具预算；新增讨论的第二次语义只使用后者剩余预算。两阶段合计上界为 7 轮模型、14 次工具，均不自动续加。全流程继续共用 40 次来源操作：Search／Fetch 的实际供应商请求尝试，加上仓库核查 LOOKUP.get（包含该层缓存读取）；供应商 Chat 模型请求另外计数，GitHub 重定向的实际 wire HTTP 另外审计。

新 GitHub 候选通过 `intake_discovered_repositories` 登记。服务端验证已有来源／证据／快照，按仓库身份及原 ref／子目录去重，最多累计 4 个仓库，先固定 SHA、读取该范围 README；关系一律从 uncertain 开始。搜索引文不进入强关联证明，读取成功也不自动 confirmed。后续只有既有语义校验确认关联，才可补查实现／讨论，再由原 0.2 规则计分。新发现仓库还拒绝否定、跨论文拼接及明确 unofficial／third-party 实现冒充官方的确认；第三方依赖描述不等同仓库身份声明。实际第三方复现仍保留独立发现记录，不自动取得官方代码分或可评分独立复现身份。

Search／Fetch 快照保存供应商返回的安全字段及提取 Markdown，记录供应商 HTTP／原目标 URL；它们不是目标站原始 HTML、原站 HTTP 状态或已经验证的跳转链。最终 GitHub 归属及实现材料仍须绑定原 GitHub 客户端取得的实际 SHA／子目录来源。查询失败、正文不可读、限流、预算及格式／证据校验错误均留审计；恢复复用失败发现时也保留 partial，不因后续没有语义目标而改成 completed。GET／离线重放不会重新付费搜索。

**2026-10-19 当前 1.3 真实验收：两篇均从固定原 PDF fresh 解析，CoT execution completed，DataMIL execution partial；两篇来源机器审计均通过，不将审计通过写成执行全部完成。** 本地全量测试为 **1,706 passed**，Ruff 与来源审计通过；未修改原 0.2 规则。

| 样本 | 实际 Search／Fetch | 登记 URL／模型调用 | 关联与原代码试验分 | 执行状态 |
|---|---|---|---|---|
| CoT／paper_005（Merrill／Sabharwal，2310.07923v5） | 3／3 | 18／4 | 本次范围未确认官方实现，4 分 | completed |
| DataMIL／paper_037 | 2／4 | 8／7 | UT-Austin-RobIn/datamil verified，7 分 | partial：一项 Fetch 供应商 404 |

DataMIL 使用实际固定提交 `f8fc4abccacd1e2b070e68128a7f2bce98f4b303`，抽读 `datamil/eval_libero_parallel.py` 与 `pyproject.toml`。仓库关联阶段 completed（1 模型／0 工具），新确认后补查得到两条讨论，第二语义阶段 completed（2 模型／0 工具，含一次有界输出修复）；旧 garage 重复 README／datamil 文本预算错误没有在本轮重现。全流程 23 次来源操作＝17 次 GitHub＋2 Search＋4 Fetch，保存 27 个来源／84 个可达快照；4 个登记 URL 未读，不作无实现断言。官方 issue #3 为 failure_reported／relevant／open，条件只称原帖 reported values、指标未命名；#2 为数据集托管建议，discussion_only／relevant／unknown。讨论均不计分，也未执行论文代码。

发现摘要以服务端实际查询／读取／候选登记计数生成；模型自由理由只作为审计中的 `model_rationale_unverified`，不把对未读页的推测当公开事实。仓库 Prompt 1.10 优先使用已提供的固定版本 README，不为重复读取增加请求；未明示的讨论指标保留未知。1.2 及更早运行、原失败输出和修复尝试分别留档，不能由最新结果覆盖。DavidFarago/state_tracking_llm_benchmark 仅引用 CoT、PromtEngineer/system1-vs-system2 等负例不因搜索命中变成官方仓库；本次未确认官方实现也不等于全网无代码。

实际记录与逐篇复核见 `outputs/code-external-discovery-20261019/backend-discovery-v1_3/` 的 summary、artifact-audit.json 与 DataMIL 独立 proof review。正式人工 Gold、前端人工联调、非 GitHub 平台和实际代码复现实验仍分别待办。
