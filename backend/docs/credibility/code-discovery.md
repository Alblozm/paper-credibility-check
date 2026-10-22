# 自动外部代码发现

## 目标与边界

论文正文没有代码链接，或现有入口无法确认实现时，后端先启动有预算的外部发现，再进入现有仓库核查。沿用 0.2 试验评分；搜索命中本身不产生代码可用性或复现加分。默认使用当前 DeepSeek 凭据调用官方 Search / Fetch API，不新增密钥。

数据流：新解析的论文身份 → DeepSeek 选择受控搜索查询 → Search 返回候选 → DeepSeek 选择候选页面 → Fetch 读取正文与链接 → 带原文引用的候选分类 → 未确认的仓库登记 → 固定提交 README 关联判断 → 实现材料与公开讨论核查 → 原评分器。

搜索、网页正文及仓库内容都作为不可信材料；它们不能扩大工具权限、执行程序或设置分数。第三方相关基准、阅读列表和真正针对本文的复现代码分别记录。搜索失败、限流、网站不可读及预算耗尽保留为覆盖缺口，不能表述成不存在代码。

## 实现约定

- 发现作为代码检查之前的独立阶段，解决当前无候选时语义模型直接跳过的问题。已有可用实现时默认跳过额外搜索。
- 模型选择启动由论文题名与 arXiv ID 构造的身份查询组；后端在同一次工具操作内执行其余已登记查询，每条最多一次。读取目标只来自搜索结果或已读取页面的真实链接，并须为公网 HTTPS。搜索摘要只能引导选择，确认使用实际读取的正文。
- 查询、正文、工具调用、模型请求尝试与最后分类都存不可变快照；报告保存来源引用。GET 和重放只读已存结果，不重新搜索。
- 发现与已有核查共享每篇 40 次外部来源请求；发现有单独、公开的模型轮次及工具预算。默认至多 3 次查询、4 次页面读取、20 个候选、4 轮模型请求、8 次工具调用。原仓库语义核查仍使用原来的 3 轮 / 6 次工具预算。
- 新发现仓库一律以未确认关系登记，继续走 GitHub 公共仓库证明、固定提交和 README 的证据验证；搜索结果与 Fetch 的 GitHub 页面不能代替固定提交的源码证据。
- 公开发现摘要由后端统计生成，写明实际查询、页面读取、通过证据校验的候选与未读 URL 数；模型自由文字解释仅保存在未验证审计字段中，不能当作未读页面的核查结论。未确定的候选也不输出未经证实的内容断言。
- 新结果使用字段合同 0.5 的 `code_discovery`；旧版本投影继续保持旧结构。配置和版本进入持久任务指纹，不包含凭据。

## 验收

自动测试覆盖：无正文链接仍启动原生工具调用；查询→页面→仓库的权限链；伪造来源/引文拒绝；阅读列表与仅引用本文的基准不进入实现确认；空搜索与检索失败分开；共享预算；固定提交/子目录不变；断点与离线重放；旧接口结构兼容。

真实验收使用冻结样本的原始 PDF 重新解析，不读取旧代码结论。CoT 指 Merrill / Sabharwal 的 *The Expressive Power of Transformers with Chain of Thought*（2310.07923v5）；记录实际模型、Search、Fetch 与 GitHub 请求，逐一核对仓库归属和未找到原因。是否找到官方仓库与闭环是否运行分别报告。

## 取舍与后续

固定查询和有限跳数使请求成本、证据来源与失败边界可审计，召回率会受搜索索引及网站访问情况限制。先完成这条有限闭环；规模扩大后再评估跨任务缓存、其他搜索服务、按论文领域的查询扩展和更长的作者主页链路。每次扩展均须维持来源与评分之间的证据验证。

当前实现与真实验收见下节。`completed` 表示声明的限定计划完成，始终与全网覆盖、实际执行复现实验分开。

## 1.3 阶段验收（2026-10-19，历史）

当前发现版本 `code-discovery-1.3`，代码语义提示 `credibility-external-code-1.10`，适配器 `deepseek-code-discovery-1.1`。两篇都重新解析冻结的原始 PDF，并调用真实 DeepSeek：

| 样本 | 实际检索 | 结果与原规则代码分 | 覆盖状态 |
|---|---|---|---|
| CoT：2310.07923v5 | 3次Search、3次Fetch，登记18个候选URL | 未确认官方实现，4分；未读URL保留未确定 | completed，限定计划完成 |
| DataMIL | 2次Search、4次Fetch，登记8个候选URL | 新确认 `UT-Austin-RobIn/datamil`，3→7分；固定SHA并实际抽读源码／依赖，审核两条新讨论 | partial，额外候选Fetch服务返回404 |

DataMIL 的仓库审核与新增讨论审核两个 pass 都 completed；公开复现差异只是用户报告，两条讨论仍不参与评分。1706项测试、Ruff、冻结源码／来源快照／实际请求轨迹审计均通过。完整结果保存在工作区 `outputs/code-external-discovery-20261019/backend-discovery-v1_3/`，其中 `report.md`、`verification-summary.json` 和 `artifact-audit.json` 可直接复核；早期失败与重试目录原样保留。

## 代码入口

| 模块 | 职责 |
|---|---|
| `app/credibility/code_discovery_provider.py` | 官方 Search / Fetch 适配器，公共地址、凭据、超时、大小与预算边界，保存供应商观察 |
| `app/credibility/code_discovery.py` | 原生工具循环、查询组、可读正文及父快照、URL 登记、候选分类、引文验证、最后一轮修复与后端事实摘要 |
| `app/credibility/code_checks.py` | 新候选以 uncertain 登记；复用固定 SHA 的 README、实现材料及公开讨论采集 |
| `app/credibility/code_model.py` | 同仓库／提交／子目录引用权限、明确实现归属、第三方声明防错、讨论与原帖指标判断 |
| `app/credibility/execution.py` | 在原语义审核前启动发现，确认后补查和新讨论第二轮，共享来源预算并保存全部阶段 |
| `models.py`、`shell.py`、`locate.py`、`snapshots.py` | 0.5 结构、旧版投影、全部 ID 闭合与离线快照验证 |

默认 4 轮发现请求中，读取到材料后优先输出最终 JSON，保留最后一轮做一次引用／结构修复。修复不增加来源权限、请求预算或评分字段。代码块与数学公式不参与 Markdown 正文去转义；原供应商文本始终保存在父快照，同一来源再次读取时使用同一派生正文。

### 1.4／1.5 的输出与读取边界

发现器 1.4／适配器 `deepseek-code-discovery-1.2` 为可读正文登记 `span_id` 和原始区间。当前 `code-discovery-1.5` 的新模型输出只允许 `source_id + span_id`，不能携带自由转写的 excerpt；后端填入该区间的原文，再执行原有来源、论文身份和实现关系校验。旧归档继续兼容精确引文解析。这样不需要模型重新转写 Markdown／数学转义。工具响应和修复请求都提供候选实际可用的来源／跨度映射；未读候选可省略或保持 undetermined 且不填引用，不能借 Search 摘要补负分类。原始输出、所有失败尝试、span 登记及解析后的输出分别存快照，审计器根据 `model_citation_protocol=registered_spans_only` 重验原始模型协议，并从来源重新推导原文，不能靠保存后的引文自证正确。DSML 文本只记录为协议错误，不执行其中内容。

父页面链接只负责登记候选。要用父页证明某个子链接的实现关系，引用本身必须保留本论文身份、局部明确的实现声明及准确指向该候选的 Code 链接；不能把整页的论文归属借给依赖或基线仓库。

Fetch 的 HTTP 200 与正文可读性分别记录。浏览器验证、CAPTCHA 或登录拦截保留供应商原始成功响应，但派生来源为 `access_denied`，记录 `content_status=blocked` 和具体原因，不提供引用权限，并保持 partial。额外 PDF 候选不由代码发现阶段下载；已有匹配实现线索后，不再读取额外元数据页，跳过原因留审计，不冒充已经读过，也不清除先前失败。模型、工具和来源预算均未增加。

## 调用方式

部署使用真实 DeepSeek 模型与可用的 PDF 解析器（`LLM_PROVIDER=deepseek`、`PARSER_PROVIDER=generic`）；已有 `DEEPSEEK_API_KEY` 同时用于模型及 Search / Fetch，`GITHUB_TOKEN` 用于后续公共仓库读取。`CREDIBILITY_CODE_DISCOVERY_ENABLED` 默认开启；其他发现预算配置见 `.env.example`。发现来源读取总计仍受 40 次共享上限限制，单次 Search / Fetch 最多 20 秒，发现阶段最长 600 秒，错误不会无限重试。

发现阶段使用原生函数工具；`CREDIBILITY_CODE_TOOL_MODE=prefetch|native_tools` 继续控制后续仓库／讨论语义阶段。使用当前正常的可信度创建接口即可启动闭环。

正常提交可信度任务即可自动触发，不需要前端单独调用搜索接口：

```bash
curl -X POST http://127.0.0.1:8000/v1/credibility/assessments \
  -H 'Content-Type: application/json' \
  -d '{"schema_version":"credibility-0.5.0-draft","analysis_mode":"credibility","source":{"type":"arxiv","arxiv_id":"2310.07923v5","upload_id":null},"model_id":"model_standard_v1","rubric_version":"0.2-provisional-complete"}'
```

用返回的 `assessment_id` 读取 `GET /v1/credibility/assessments/{id}`。在 `report.code_discovery` 查看查询、实际读取次数、候选关系、证据 ID 和未完成原因；确认后的仓库、实现材料及讨论仍在 `report.checks.code`。已有充分实现证据时发现状态为 `not_needed`；未配置模型/检索能力时为 `disabled`，都与搜索后没有找到不同。旧 0.1–0.4 请求继续保留旧响应结构。

只验证代码模块、避免重复运行引用与论点检查时，维护者可用冻结样本脚本；必须使用新目录：

```bash
.venv/bin/python scripts/verify_code_usability.py \
  --samples /absolute/path/to/samples-frozen.json \
  --sample-id paper_005 --output-dir /absolute/path/to/new-run \
  --mode native_tools --execute
```

这个命令重新解析原始 PDF，再调用生产代码核查与原评分器。它不会执行仓库代码；代码模块的 0–10 分不能当作整篇论文的三项总分。

## 1.5 最终专项结果（2026-10-19）

完整十篇最新结果见 [代码可用性手册](code-usability.md) 末节，归档在工作区 `outputs/code-partial-fixes-20261019/runs-v3/`。严格片段协议已在 CoT／Object／DataMIL 等真实 DeepSeek 运行中通过。CoT 仍为 partial／4分，原因仅为一项候选 Fetch 服务404；Object 为 partial／7分，原因是额外长页截断和候选上限，官方固定版本实现已经确认。不能把 Fetch 服务状态推断成原站资源不存在。所有旧原始失败与各版不可变源码均保留。
