# 代码关联、实现材料与公开复现线索核查

当前代码分支会核对论文给出的项目页和 GitHub 仓库，固定代码版本／子目录，记录实现入口、依赖说明及数据／权重线索，并用真实模型解释必要的仓库关联和公开讨论。这里的“可用”表示取得关联明确的公开实现材料；`runnable_status="not_executed"` 明确表示没有安装依赖、启动代码或复现实验。`0.2-provisional-complete` 数值规则保持不变。

公开默认合同现为 `credibility-0.5.0-draft`，在原代码核查上新增独立的 `code_discovery`；旧 0.4 保留原公开字段及有界完成语义。自动发现当前实现与待验收边界见本文末节和[自动外部代码发现](code-discovery.md)，旧五维接口不受影响。合同见数据仓库 `docs/project/01_统一规范/可信度核查报告字段合同_v0.5.md`；模型工具协议、配置与权限详情见 [external-tools.md](external-tools.md)。

## 自动操作流程

1. 从当前解析的正文、附录及已解析补充材料定位代码声明和原始链接，保留真实 chunk/page 证据。支持 `dataset and code are available at` 等并列声明，不再只识别 `code is available`。
2. 沿论文明确关联的静态项目页读取 HTML。使用 HTMLParser 解析嵌套标签、无引号属性及相对／协议相对链接，再按项目页 URL 解析目标；只有能解析成 GitHub 候选的明确 Code／Repository 等链接进入仓库判断。页面须标识当前论文。基线／相关工作区域中的 Code 链接不因相邻页面存在当前题名而自动取得本论文关联。
3. 访问公开 GitHub 元数据，解析论文／项目页实际指定的 ref 和子目录。`/tree/<SHA>/path` 直接核对该 SHA；branch/tag 与子目录有斜杠歧义时，通过有界 matching-refs 查询识别最长合法前缀，再解析实际 SHA。显式 ref 失败不换用默认分支；只有原入口没有指定 ref 时才解析当前默认分支。
4. 在该 SHA、该子目录读取 README 和实现目录，并最多抽读一个实现文件和一个依赖清单。入口是 `/blob/<ref>/path/README.md` 时，在同一 ref 的 README 父目录寻找实现；入口明确指向源码文件时仍只核对该文件。显式 ref 解析失败不换用 HEAD。实现文件须位于指定子目录；不会用另一目录的代码、明确 vendor／third_party／docs／tutorial／tests 路径或已知空文件作为本论文实现证据。GitHub 标准换行 base64 正常解码。`setup.py` 保留为依赖／包装清单，不能单独构成研究实现；唯一候选若是实际读取后只有注释、docstring、导入或字面量包元数据的 `__init__.py`，也不据此给出实现可用。含真实函数、类或初始化逻辑的 `__init__.py` 仍可作为实现。抽读优先 README 命令及常见入口，其次是非 `__init__.py` 的实际模块，不只抽读包导入文件。没有入口或依赖说明会保留缺项，不新增“必须具备所有材料才能获得原代码分”的评分条件。
5. 仓库关联为 uncertain、讨论相关性／处理结果不明或成功失败并存时，按配置进入 `prefetch` 或 `native_tools`。模型引用必须来自当前允许的真实来源，仓库文件证据受 SHA／子目录约束，讨论证据受仓库和线程约束；不能借另一个线程或其他版本的 README 完成确认。
6. 待关联仓库与已有讨论同时存在时，先做仓库关联，再只审核确认属于本论文仓库的讨论；恢复任务也不重新把已排除依赖的讨论送入模型。模型经证据校验后新确认的仓库，进入 `complete_confirmed_repositories`：复用已保存元数据及固定版本，补查尚未读取的实现材料和公开讨论，刷新代码可用性。新增或延后的已确认仓库讨论仍需解释时，最多再执行一次语义阶段，使用原剩余预算。
7. 后端按原规则计分并保存结果。所有原有来源、失败观察、引文和模型阶段审计均保留；GET 只读取保存结果，不再次访问 GitHub 或模型。

有效语义输出已经验证／存档、但某次工具读取失败导致 partial 时，仍可以保留已有证据支持的关联并执行确定性后续采集；失败与最终 partial 不被后续成功覆盖。failed／unsupported、没有有效输出的 partial 不触发新确认后续查。缺少文档或有效正文时保持 not_run／failed，不能因为模型没有待处理目标而标记 completed。HTTP 200 只表示传输成功；README／内容 base64 损坏、缺失编码或目录 payload 结构错误均保留采集 partial，确认后补查不能仅因来源 `fetch_status=ok` 将其覆盖成 completed。

## 固定版本与来源证明

公开仓库对象继续使用 `inspected_revision` 表达实际提交 SHA，没有新增仓库字段。原始 URL、requested revision、子目录、ref 是否已解析及续查完成情况保存在内部 `CodeCheckResult.followup_state`／检查点，恢复时保留；不直接序列化进 `report.checks.code`。

模型只读工具沿用这个固定范围：已核验 SHA 不被 HEAD 覆盖；读取文件的相对路径限定在所指子目录；显式 SHA 与返回 SHA 不一致时拒绝继续。仓库确认引文权限和最终校验同时核对版本／子目录，旧来源或同仓库其他目录不能代替当前代码材料。来源 URL、快照及实现观察摘要可用于前端展示版本和范围。

`code_implementation_inspection` 是程序汇总的观察快照，不冒充一次上游 HTTP 响应。其规范数据包含：

| 字段 | 用途 |
|---|---|
| `repository / original_url / requested_revision / inspected_revision / inspected_subdir` | 绑定原入口、实际 SHA 和检查范围 |
| `observation_type="aggregate_inspection"` | 区分程序汇总与上游响应 |
| `inspection_status` | 汇总涉及的预定读取是否完整；失败时保留 partial |
| `implementation_paths / entrypoint_paths` | 范围内实现文件及入口候选，不声称已运行 |
| `excluded_implementation_paths` | 实际抽读后排除的唯一占位包初始化文件；包装清单另列于依赖路径 |
| `dependency_manifest_paths` | 实际目录中看到的 requirements、pyproject、environment 等路径 |
| `readme_source_id / data_weights_setup_notes` | README 原来源与其中已有的安装、数据、权重等文字线索 |
| `inspected_files[{path,source_id,fetch_status,content_read}]` | 实际抽读文件、读取状态及内容解码结果 |
| `source_ids / source_code_executed / runnable_status` | 可回查来源；执行标志为 false，状态为 not_executed |

摘要附属的原 GitHub 响应和源内引文继续存入 sources／evidence。摘要对象保存成功不等于所有上游读取成功，必须同时检查 `inspection_status` 和逐来源状态。依赖／数据说明只证明文本存在，未验证依赖可安装、下载链接可用、权重正确或指标可重现。

## 完成、覆盖与评分

| 状态／字段 | 当前含义 |
|---|---|
| `check_status=completed`、对应 execution completed | 声明的有界采集与必要语义计划已经执行完，没有待完成步骤或未恢复的技术失败 |
| `check_status=partial` | 预定来源读取失败、共享请求／模型预算不足、必要语义阶段未完成等；保留已取得事实 |
| `check_status=not_run / failed` | 输入不足未执行，或无法形成有效分项；不因没有模型目标而改成 completed |
| `checks.code.scope_complete=false` | 未穷尽全网、附件或全部讨论；不会仅凭这个字段把已完成有界计划标成失败 |
| `code_availability=available` | 本论文关联已确认、仓库可访问且范围内取得实现材料；不是代码可运行或系统复现成功 |

完整引用和原始讨论仍可查看。多论文仓库仅依据 README 明确 `Paper:`／`Papers:` 论文标签、代码归属句及紧邻的论文链接列表，或明确 Supported／Implemented Papers 节识别；不把首个二级标题前的整段引言作为授权。Supported Models、基线／骨干方法表和 References 中的 arXiv 链接不作为仓库支持论文的证明。README 内 python／bash 等命令只有对应路径真实存在于已取得的范围内目录时才记为入口，不执行命令。shared repository 的帖子须有本论文题名、arXiv ID 或题名前缀中的独特模型名，不能只凭 `your paper`、表号、指标或同一仓库推断相关；模型名简写只用于讨论归属，不用于仓库确认或免读材料。关闭 issue 不表示已解决；成功和失败、数据集／硬件／指标等条件分别保存。作者身份与独立性不由模型猜测。当前讨论继续 `scoring_eligible=false`，未经正式核验的公开报告不自动改变代码分。

内部语义审计 `source_read_coverage` 分别记录原文／保留字符数、原始区间、正文完整性、分页完整性及是否为最终判断的必要来源。长 README 保留引言和论文身份附近的原始区间，省略区间带标记，不能跨省略拼引文。只有已凭当前原文证据排除的仓库、完整归档中没有本论文身份、且不存在读取／分页错误时，其未读余文可从最终必要计划中排除；这不把 `text_complete=false` 改成已读完。确认仓库及所选讨论的必要材料不足仍 partial，未选择的讨论 ID 单独保留。具体 issue/comment URL 必须唯一对应当前线程的原始记录，不能回退引用列表中其他线程。

Prompt 1.12 的原生语义流程对供应商拒收的输出，在原有剩余模型轮次内最多重试一次，强制不调用工具，继续使用当前目标和已有证据。失败请求同样消耗轮次并永久保留审计；后续 JSON／数字引文修复也按全部请求尝试扣预算。截断或无效工具响应整次拒收，不能抢救其中判断或执行坏调用。诊断只保存有限枚举原因、finish reason、安全整数 usage 等协议事实；不会保存任意异常消息或隐藏推理。有效输出恢复后仍须通过原有证据校验，来源截断／分页缺口不会随模型重试而消失。

API `schema_version`、pipeline／checker／Prompt 版本和计分 `rubric_version` 分别管理。下述历史六篇验收使用 `credibility-0.4.0-draft`、`credibility-0.7-code-evidence`、`code-checks-2.3-implementation-shells`、`credibility-external-code-1.7`；它们进入版本身份及审计，实际运行以各 summary 保存的代码 SHA／versions 为准。相同固定 checks 在四种传输版本下产生相同 0.2 试验分；规则字节、权重、阈值和公式未改。分数不能作为技术执行完成的依据。详见 [provisional-scoring.md](provisional-scoring.md)。

## 请求、模型及覆盖预算

基础检查、模型工具和确认后采集共享**每次执行最多 40 次来源读取操作**：调用 `LOOKUP.get` 即计一次，包含该层缓存命中。恢复时可从有效快照重建已取得事实，不为重放再花网络预算。模型读取已载入快照或返回已有 SHA 不伪造一次新 HTTP。

40 次读取不是 40 次网络 HTTP 的上限：传输层可以因 GitHub 仓库迁移等产生有界重定向，真实 HTTP 的每个跳转由单独审计记录。`external_request_count` 与 `http-audit.json` 的 GET 数量采用不同单位，评估时分别报告。缓存、匿名／认证 GitHub 配额及 403／429／超时处理沿用 [external-tools.md](external-tools.md)，不通过开新预算绕过失败或限流。

模型两个语义阶段共用 `CREDIBILITY_CODE_MAX_ROUNDS`（默认 3）和 `CREDIBILITY_CODE_MAX_TOOL_CALLS`（默认 6），第二次只使用剩余模型轮数／工具预算和来源读取余额；无余额时可只解释已有合格快照，缺少必要材料时保持 partial。每条来源给模型最多 15000 字符，全部文本默认 60000；截断和缺页明确留档。合并审计保留各阶段输入、工具调用、有效输出、失败尝试、剩余预算和新增讨论 ID，不覆盖第一阶段轨迹。Prompt 1.7 要求条件中的架构／代码标识保留原拼写；数字条件未获来源支持时，可在剩余轮次内进行最多一次无工具输出修复，删除无依据数字、改回原标识或保留未知。修复仍经过原有强引文、数字条件及范围校验，共享轮次／工具／来源预算不增加；没有有效输出或无剩余轮次时仍 partial。

每个语义阶段最多选择 4 个待关联仓库、8 条需要解释的讨论目标；8 条指讨论，不包含仓库目标。讨论队列从真实原文识别“复现困难投诉”，其次识别运行／训练／下载／加载等执行困难，其余目标随后处理；同优先级保持原采集顺序，不按字符串 ID 重排。优先级只决定有限预算下先读谁，不自动判定相关性或复现失败；未选讨论不写成已经语义核查。第二阶段继续只处理本次补查新增的目标。

基础范围仍为最多 4 个仓库、4 个官方项目页、每仓库最多 2 页 issue（每页 20 条）、每 issue 最多 2 页评论（每页 100 条），还受共享 40 次读取约束；原生单次评论工具只读取首批 30 条。分页上限是覆盖限制，不能写“全部讨论已核查”。当前不支持 GitLab／Bitbucket、GitHub Discussions、JavaScript 渲染或 PDF／ZIP 补充包递归解析。

## 运行与验收

完整产品流程仍使用原 POST／GET 接口；当前自动发现结果应请求 `schema_version="credibility-0.5.0-draft"` 和 `rubric_version="0.2-provisional-complete"`，复核原六篇历史结构时显式使用 0.4。代码专项可以独立验证，避免把主张／引用的模型消耗混入本次证据。

`scripts/verify_code_usability.py` 校对冻结样本清单与原 PDF SHA，从原 parsing lineage 取得论文身份后重新解析 PDF；不读取旧代码结论或模型输出，执行当前生产代码分支，并调用同一 0.2 代码计分函数。它不执行主张、引用检查，也不计算整篇总分。输出目录必须不存在，避免覆盖历史运行；每篇使用新的进程和目录。

以下命令会调用已有 `.env` 的真实 DeepSeek 配置并产生模型费用；在 backend 仓库执行，不需要打印或覆盖原 API 密钥：

```bash
RUN_ROOT=outputs/code-usability-20261019
LLM_PROVIDER=deepseek .venv/bin/python scripts/verify_code_usability.py \
  --samples "$RUN_ROOT/samples-frozen.json" \
  --sample-id paper_038 \
  --output-dir "$RUN_ROOT/manual-geofar-new" \
  --mode native_tools \
  --execute
```

显式选择预抓取模式对照时使用 `--mode prefetch` 和另一个新目录，不能将这次调用记为原生工具通过。若没有疑点，允许模型调用为 0／`not_needed`；如需单独证明供应商原生工具闭环，使用 [external-tools.md](external-tools.md) 的独立 probe 命令，不能把没有工具请求的论文运行当成协议闭环证明。

每个 run 保存 `input-context.json`、`code-result.json`、`summary.json`、`http-audit.json` 和 `artifacts/`。summary 记录 PDF／输入／实现 SHA、Rubric 摘要、pipeline／checker／Prompt、HTTP／模型调用、执行状态、代码分与来源证明完整性。原始 provider reasoning、凭据和 Authorization 不写入报告。脚本 `status=passed` 只表示该次有界计划及技术验证通过；不是人工准确率或复现实验通过。

本轮六篇冻结样本为 LoRA v2、paper_001（共享仓库）、paper_038（GeoFAR 项目页）、paper_037（DataMIL／基线负例）、paper_005（CoT 理论候选）、paper_013_arxiv_v2（历史失败／引用依赖负例）。清单 `is_human_gold=false`，预期是预先冻结的 AI 辅助验收提示，不输入生产判定器；旧失败标签不作正确答案。

**2026-10-19 最新状态：检查器 2.3／Prompt 1.7 的最新六篇有界技术执行均 completed；这是原 1.7 五篇已完成运行加 LoRA 一次同配置、同冻结源码的独立 fresh 重试，不能称为首次同批六篇全部通过。** 本地全量自动化测试 **1,471 passed**，另有 2 条既有依赖弃用警告。

| 最新样本 | 代码可用性 | 原 0.2 代码试验分 | 技术状态 |
|---|---|---:|---|
| LoRA v2（独立重试） | available | 7 | completed |
| paper_001／Tangent | available | 7 | completed |
| paper_038／GeoFAR | available | 7 | completed |
| paper_037／DataMIL | unable_to_verify | 3 | completed |
| paper_005／CoT | not_found_in_scope | 4 | completed |
| paper_013_arxiv_v2／Object-Centric | unable_to_verify | 3 | completed |

LoRA 实际读取 `loralib/layers.py` 与 `examples/NLU/docker/transformers-cpu/Dockerfile`，前者是实现抽读，后者是容器依赖观察。共保留 15 条讨论，其中仅 8 条进入本次模型语义审核；输出为 5 条 failure_reported、10 条 discussion_only，后者包含 7 条只完成基础规则判断的帖子，不能据此声称这些帖子的相关性、条件及处理结果已逐一获模型核验。全部讨论仍不参与评分。GeoFAR 保留 relevant／discussion_only／open；Object-Centric 对 Spriteworld 保持 uncertain，不把引用的 renderer 依赖认成本论文官方实现。

失败历史完整保留：`acceptance-final/`（2.3／1.6）原六篇为 5 completed、LoRA partial，数字条件将来源 `resnet18` 写成 `ResNet-18` 触发强校验；`acceptance-rerun-v1_7/` 原六篇仍为 5 completed、LoRA partial，该次客户端 `LLMOutputInvalid` 未取得可验证原始 JSON，具体上游原因未知。不能根据后续诊断追溯判定先前响应被长度截断。`acceptance-rerun-v1_7-retry/lora_2106_09685v2/` 是唯一追加重试：两次模型响应均 finish_reason=stop，第一次输出 14,396 tokens，未被截断；数字条件 guard 对 code_disc_13 触发一次无工具修复，第二次仍通过原强校验，预算及评分规则未放松。

原始运行、冻结源码、逐篇证明复核和最新选择／机器审计位于 `outputs/code-usability-20261019/`，最新机器审计 `acceptance-latest-six-artifact-audit.json` 通过；总览见该目录 `README.md`、`report.md` 与 `verification-summary.json`。原轮次与独立重试分别保存，不将失败覆盖成成功。这是代码专项技术验证，未运行主张／引用或计算整篇新总分；未安装依赖或实际执行论文代码。真实“模型新确认仓库→补查得到新讨论→第二语义阶段”正向闭环仍未在这些实跑中自然触发，只有自动化回归覆盖；额外 paper_009 原 PDF 下载 403，未作为补齐样本。正式人工 Gold、前端人工联调、非 GitHub 平台及实际运行论文代码仍分别待办。


## 自动发现 0.5：当前实现与验收范围

当前代码检查器为 `code-checks-2.4-discovery-intake`，仓库语义 Prompt 为 `credibility-external-code-1.10`；发现器为 `code-discovery-1.3`／`credibility-code-discovery-1.3`、供应商适配器为 `deepseek-code-discovery-1.1`，pipeline 为 `credibility-0.8-code-discovery`。前面的 2.3／1.7 六篇完成表、两批 5/6 失败历史与 LoRA 独立重试保持原证据含义；它们没有执行本次 Search／Fetch 自动发现，不能当作当前自动发现版本的真实通过数。

原论文没有代码链接、或已有候选不能确认实现时，后端可以先按当前题名／arXiv ID 发起有界 Search，再由模型选择已登记候选作 Fetch，按正文原引文区分官方声明、实现候选、第三方复现、第三方相关资料和阅读列表。模型不能新编查询、URL 或仓库；搜索摘要只提供线索。新仓库一律先 uncertain，沿用实际公共仓库元数据→固定 SHA／子目录 README→语义关联→实现抽读／公开讨论→必要的新讨论语义阶段。搜索材料不代替固定版本源码，未确认的候选不提供原官方代码可用分。

发现默认上限 3 查询／4 页面／20 候选、4 模型轮次／8 工具；仓库语义仍是原 3 轮／6 工具。来源预算继续全程 40 次，新增 Search／Fetch 供应商请求与原 LOOKUP.get 共同计入；缓存、真实 HTTP 跳转及模型调用分别审计。取回的 Markdown 是供应商提取内容，不能声称拿到了原站 raw body／origin HTTP／完整跳转链。所有失败和未完成目标保留具体缺口，重放已保存结果不再次搜索。

配置、调用顺序、来源权限与失败状态详见 [code-discovery.md](code-discovery.md) 和 [external-tools.md](external-tools.md)。新报告顶层 `code_discovery` 保存查询、候选分类及状态、来源／证据／仓库绑定、预算与审计；显式 0.1–0.4 返回原公开投影。计分仍完全采用原 `0.2-provisional-complete`，讨论继续不计分，也不执行仓库代码。

**2026-10-19 的 1.3 阶段真实验收已留档：CoT completed；DataMIL 官方实现 verified、代码试验分 7，但一项 Fetch 供应商 404 使 execution 保持 partial。** 两篇来源机器审计通过，不能写成两篇 execution 全部 completed。本地全量 **1,706 passed**，Ruff 与来源审计通过，原 0.2 Rubric 不变。

| 样本 | Search／Fetch | 登记 URL | 模型调用 | 官方实现／原代码试验分 | execution |
|---|---|---:|---:|---|---|
| paper_005／Merrill–Sabharwal CoT，2310.07923v5 | 3／3 | 18 | 4 | 本次范围未确认，4 | completed |
| paper_037／DataMIL | 2／4 | 8 | 7 | UT-Austin-RobIn/datamil verified，7 | partial |

DataMIL 从原 PDF fresh 解析，新增发现没有使用旧代码结论或模型输出。三个原论文链接的基线仓库均 rejected，官方仓库固定 SHA `f8fc4abccacd1e2b070e68128a7f2bce98f4b303`，实际抽读 `datamil/eval_libero_parallel.py` 和 `pyproject.toml`，实现观察 completed、`runnable_status=not_executed`。本轮覆盖此前六篇未自然触发的正向闭环：发现官方候选→仓库语义确认→补查实现／两条新讨论→第二次语义。仓库阶段 1 模型／0 工具，讨论阶段 2 模型／0 工具（含一次预算内输出修复），均 completed、无不完整工具读取。7 次总模型调用含发现的 4 次，未扩大原 4＋3 轮预算。

DataMIL 共 23 次来源操作（17 GitHub＋2 Search＋4 Fetch），27 个来源、84 个可达快照；8 个登记 URL 中 4 个未读，候选7供应商 404 保留为技术缺口，不推断原站资源不存在。官方 issue #3 是 LIBERO-10 结果差异报告，failure_reported／relevant／open；所列数值保留原帖表述，指标名称未明示。#2 是数据集托管／发现建议，discussion_only／relevant／unknown。维护者角色不自动证明论文作者或独立性，讨论全部不计分。CoT 没有确认官方代码，仅完成声明范围，不能据此宣布全网无代码。

发现 1.3 的公开摘要使用服务端实际计数；模型理由只保存在审计的未验证 rationale 中。仓库 Prompt 1.10 优先复用已提供的固定 README、保留未命名指标；旧截断／重复读取问题本轮未重现。历史 1.1／1.2、原 1.6／1.7 和重试目录完整保留，不以最新运行改写旧失败状态。最终记录见 `outputs/code-external-discovery-20261019/backend-discovery-v1_3/` 的逐篇 summary、artifact-audit.json 与独立 proof review。正式人工 Gold、前端联调、非 GitHub 托管、实际运行和独立复现实验继续各自待办。

## 1.5／1.12 修复后的十篇专项（2026-10-19）

最终 pipeline 为 `credibility-0.11-span-citations`。1838 项全量测试、Ruff及十篇来源／固定版本机器审计通过；相同十份原始 PDF 全部 fresh 解析，9篇实际请求真实 DeepSeek（25次尝试、23次协议接受），1篇走确定性官方入口路径。整批结果为 **6 completed、4 partial**，所有分数沿用原规则。

Object 已取得同一固定提交下的实际实现，3→7分，剩余 partial 是额外长页截断及候选上限。CoT 的来源片段协议正常，4分，剩余 partial 是 Fetch 服务对一项候选返回404。LoRA 为7分，但两次16000 tokens截断导致所选8条讨论接受0条，保持partial。Limits 为4分，因共享60000字符上限截断保留partial。DataMIL、Ctrl-World、Tangent、GeoFAR、Distributional Unlearning均completed、7分；Fairness完成本次限定搜索、3分。未读URL与未审讨论不计为已审核。

最终整批、失败及历史对照见工作区 `outputs/code-partial-fixes-20261019/`：`report.md`、`summary.json`、`runs-v3/`、`implementation-v3/`、`audit-v3-*.json`、`review-v3/`。历史初轮及第二轮没有替代最终失败项。当前仍只核查代码证据，论文仓库代码未运行，第三项暂未实施。
