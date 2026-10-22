# 自动多来源引用核查

引用检查现在在 Crossref 主链路之后，自动对仍为 `unverified / not_checked` 的条目补查 arXiv 和官方出版来源。该分支直接调用确定性检索和匹配代码，不需要大模型工具选择，不读取此前人工定位的 37 条补查清单。原 `0.2-provisional-complete` 数值规则保持不变。

独立 API 默认使用 `credibility-0.3.0-draft`，新增 `report.reference_verification_summary`。显式请求 0.1／0.2 仍返回各自原有 JSON 结构；数据库可以保存完整报告，再按调用版本投影。未改前端页面，前端后续按 v0.3 合同消费新摘要。旧五维 `/v1/assessments` 不受影响。

## 自动流程与身份条件

1. 从当前解析论文提取全部可见引用，保留原始条目、论文内证据和源内位置，按元数据执行 Crossref DOI／书目查询及必要的正式版／预印本版本解释。
2. 对未确认条目检查提取锚点、私人通信、标题及已列作者。提取缺口、私人通信或识别字段不足时保留具体原因。
3. 优先使用引用中的 arXiv ID，只有已归档的同 ID 官方记录能建立该标识的身份关联，且题名、已列作者及可用 DOI 必须兼容。引用显式指定 `vN` 时，必须取得并核对该确切版本，不能用返回的最新版代替。没有 ID 时，执行最多两种有界标题＋首作者检索；返回的每个候选仍须核对完整规范题名及全部已列作者。不能用检索排名或部分关键词命中代替身份确认。
4. 若题名／年份需要旧版本解释，读取同一 arXiv ID 的官方提交历史与最多四个旧版本元数据，保留对应日期及版本证据。已有 ID 不改查另一作品。明确 `et al.` 时只容许可一一对应的已列作者子集；正常作者列表按完整兼容名单校验。原引和来源均有 DOI 时，两者须一致。
5. arXiv 没有充分日期证据时，可先保留独立于年份建立的同 ID／题名／作者身份，再用官方出版来源核对引用年份；最终复合快照同时关联 arXiv 身份和出版日期证明。无法建立显式 arXiv ID 身份时，不允许转用一个同题出版页面替代该标识。没有显式 ID，或已取得所需 ID 身份关联后，官方入口包括引用中的 URL、已存 Crossref 响应中题名及作者兼容候选的出版链接，以及由引用场所／卷号／年份识别的 PMLR、JMLR、NeurIPS 有界目录。没有通用网页搜索或任意网页遍历。
6. 官方 HTML 需同时有书目元数据和可见正文中的题名／全部作者；引用年份须与来源日期一致。直接官方报告 PDF 必须同时有独立的文档信息／XMP 题名、文档作者元数据，以及首页可见的相符题名／全部作者和所需年份；不能从引用文本反推 PDF 身份，也不能用首页提到的另一篇论文题名确认当前 PDF。目录链接和搜索命中只是入口，不直接改为 `matched`。
7. 证据成立后添加本轮 source、candidate、外部 evidence 及不可变快照，将该条目改为 `matched`；原输入和失败尝试保留。冲突条目 `metadata_conflict` 不通过补查自动洗掉。

PDF 连字、空白、重音等展示差异可规范化；真实给名／署名差异、日期矛盾和改题关系需要相应来源证据。原引用没有年份时仍保留 `parsed.year=null`，不会用后找到的年份补写原输入。核实文献身份不表示引用所在正文的主张已被该文献支持。

实现入口为 `app/credibility/references.py`，arXiv 和官方出版来源分别为 `reference_arxiv.py`、`reference_publishers.py`，底层传输为 `reference_version_transport.py`、`reference_publisher_http.py`。来源和确认对象在 `reference_sources.py` 定义；没有 LoRA／CoT 专用的论文 ID、题名或人工证据 URL 清单。

## 核实率、原因与试验分

`reference_verification_summary.verification_rate = matched_count / extracted_count`，取值 0～1；没有已提取条目时为 null。`denominator` 固定为 `extracted_references`。它表示本轮已提取集合中的文献身份核实率，不是论文可信度概率。

摘要同时包含 `extracted_count / matched_count / unverified_count / not_checked_count / metadata_conflict_count`、`extraction_complete` 及所有非 matched 条目的 `unresolved_items`。只有提取状态为 complete 且没有 gaps，`extraction_complete` 才为 true；即使已提取的 10 条全匹配，也不能据此把未知全文覆盖写成 100%。

每条未完全核实项保留 `reference_id / outcome / reason_code / reason / source_ids`，可沿 ID 打开原条目与来源快照。原因采用确定性编码：

| 原因 | 含义 |
|---|---|
| `target_not_found_in_scope` | 本次有界范围没有取得适当目标，不等于文献不存在 |
| `year_mismatch / author_mismatch / title_mismatch / identifier_mismatch` | 相应身份字段不能确认一致 |
| `version_evidence_missing` | 改题或版本关系缺少足够证据 |
| `invalid_arxiv_id / insufficient_metadata` | 标识格式或识别信息不足 |
| `personal_communication / extraction_gap` | 私人通信或提取缺口 |
| `source_fetch_failed / budget_exhausted` | 来源访问失败或补查预算耗尽 |
| `metadata_conflict / not_checked / undetermined_reason` | 旧结果无机器编码时的保守类别，原文说明仍保留 |

身份字段矛盾或缺少版本证据时，优先保留相应明确原因；除此之外，任何关联补查来源未成功，汇总原因优先为 `source_fetch_failed`，不让后续“未找到目标”覆盖先前访问失败。供应商的具体原说明和失败 source 仍全部保留。预算在条目边界耗尽时使用 `budget_exhausted`，说明仍有补查未执行。

核实率不会额外乘入试验分。原引用规则仍为 matched 10、metadata_conflict 7、查询完成但未确认的 unverified 4、not_checked／技术不可核实回退 3，取已提取条目平均值；未提取条目不虚构评分行。相同固定 checks 在 0.1／0.2／0.3 传输版本得到相同分数。新来源核实更多条目时，仅由既有映射自然重新计分。完整规则见 [provisional-scoring.md](provisional-scoring.md)。

历史任务 GET 不重新联网。缺少新摘要的旧持久化报告可以从其已保存的 `checks.references` 推导；旧自由文本不通过模型或关键词猜测更具体的原因。

## 配置、预算与缓存

在已有 `.env` 内按需添加，不覆盖原 API 配置：

```dotenv
REFERENCE_MULTISOURCE_ENABLED=true
REFERENCE_FALLBACK_DEADLINE_SECONDS=600
# 本机所有 worker 共用同一 Crossref 状态位置。
CROSSREF_STATE_PATH=/absolute/path/backend/data/crossref/state.sqlite3
```

`REFERENCE_MULTISOURCE_ENABLED=false` 可关闭新增补查，保留 Crossref 主链路。补查 deadline 默认 600 秒，在 Crossref 主链路结束后开始，并在条目／供应商边界检查；下层各自保留固定请求及等待预算，所以它不是对已经开始的 HTTP 读取进行即时强制中断。Crossref 仍有独立分支 deadline 和累计冷却等待预算，见 [crossref.md](crossref.md)。

| 来源 | 状态文件 | 主要传输边界 |
|---|---|---|
| Crossref | `CROSSREF_STATE_PATH` | 单并发，默认至少 1 秒间隔；其余配置沿用原手册 |
| arXiv Atom API | 同路径加 `.arxiv-versions.sqlite3` | 单并发、至少 3 秒间隔；每次最多 5 条、512 KiB、10 秒 HTTP 预算，最多 30 秒共享等待 |
| 官方页面／报告及 arXiv 提交历史 HTML | 同路径加 `.publisher.sqlite3` | 共享单队列、至少 1 秒间隔；最多 3 次跳转，15 秒 HTTP 预算，最多 30 秒共享等待；HTML 2 MiB、PDF 12 MiB |

成功响应缓存默认 7 天。原始 XML、HTML、PDF 字节按 SHA 校验 envelope 缓存，跨 artifactroot 读取时重新生成本轮观察快照；失败不伪装为成功缓存。官方目录补查每条最多 8 个页面、3 个目标文献。429 遵守服务端冷却要求；不能通过新建状态文件绕开正在使用的供应商限流。

官方传输仅接受声明范围内的 HTTPS 主机／路径，每次初始或重定向请求均核 URL、公开 DNS 地址和响应体上限；不信任环境代理。验证页、403、404、429、超时、超限和不完整材料均保留真实状态，不绕过网页验证，不以 HTTP 200 单独认定文献已核实。

某个来源失败后，另一个来源已充分确认同一条目时，该条目的技术缺口可以恢复；原失败 source 仍在报告和 `coverage.technical_failure_count` 中。尚未匹配条目仍有关联来源失败、未检查项或预算耗尽时，执行状态保持 partial。`completed` 不表示每次 HTTP 尝试都成功，也不要求每条引用都 matched。

阶段检查点尊重生产者的显式 `partial`：即使已发送请求全部成功，预算耗尽仍保存为 partial，不作为完整阶段复用。新任务可通过显式内部恢复重跑该分支，已经完成的其他分支继续复用；GET 不触发恢复。读取阶段缓存还会复核其真实结果状态和快照，不能仅信任旧 completed 标记。引用分支完成后，已由其他来源恢复的失败尝试不会单独迫使该分支重跑；其他分支仍沿用失败来源检查。覆盖范围有限、但生产者确实完成其声明检查时，`scope_complete=false` 本身不等于技术失败。操作见 [durable-evidence.md](durable-evidence.md)。

## 引用专项冷运行与离线复跑

使用 `scripts/verify_reference_auto.py`，从已有任务中只读 parsing checkpoint 与其 fingerprint，核对原 PDF SHA 后用当前解析器重新解析。脚本不读取旧 report／reference stage，不载入人工 37 条补查结果，不运行主张／代码分支，也不调用 DeepSeek。需要有原任务数据库、原快照根和对应 PDF。

从后端目录执行 LoRA 示例；每轮输出目录必须尚不存在，live state 及其两个派生 provider state 也必须是新路径：

```bash
.venv/bin/python scripts/verify_reference_auto.py \
  --input-db ../../outputs/be07-implementation-20261019/native-final/tasks.db \
  --input-artifacts ../../outputs/be07-implementation-20261019/native-final/artifacts \
  --assessment-id cred_842d92efbadf426dbcce36238d469afa \
  --input-pdf ../../outputs/be07-implementation-20261019/native-final/arxiv/2106.09685v2.pdf \
  --state-path ../../outputs/reference-auto-20261019/example-lora-state.sqlite3 \
  --output-dir ../../outputs/reference-auto-20261019/example-lora-live \
  --expect-entries 62
```

完成后启动独立进程，使用相同 state、新输出目录，并加 `--offline-cache`。该模式禁止顶层 HTTP getter、实际 send 和异步请求；任何缺缓存导致的 HTTP 尝试使验收失败。失败访问不入成功缓存，因此未恢复的外部故障不保证能完全离线重现；必须照实记录，不能从旧条目结论填充。

CoT 对应任务为 `cred_84983f4a99e24180bc4d9b5b93303aaa`，PDF 为 `../../tmp/pdfs/pilot-selection/paper_005.pdf`，`--expect-entries 30`。使用单独新 cold state；脚本仍核对解析 lineage 的原 SHA，不能任意换一份同名 PDF。

输出包括：

- `input-context.json`、`reference-result.json`、`reference-summary.json`、`sources.json`：本轮新输入、实际检查、按原规则计算的引用分、核实率／原因和来源。
- `http-audit.json`：实际请求方法、URL、状态、时间和缓存 lookup；provider 次数按传输入口分为 Crossref、arXiv API 和 publisher（含 arXiv 历史 HTML）。
- `summary.json`：0.3 schema、Rubric SHA、实现源文件摘要、当前流水线版本、PDF 核验、计数、率、原因汇总、引用分、来源状态和模型调用 0 标记。
- `artifacts/`：本轮输入、结果、摘要、审计及所有来源的不可变快照。

`--expect-entries` 仅核对提取计数，完整原文覆盖仍由独立提取验收确认。技术通过须完整提取、没有 not_checked 且引用维度 completed；不以所有访问尝试均成功作为条件。退出码 0 为通过，2 为未完整完成，1 为 SHA／快照／网络 guard 等断言失败。保留每轮目录；不要覆盖旧验收报告。

## 本轮真实验证记录

2026-10-19，自动引用专项的两篇真实冷运行和两次独立离线复跑均通过。每篇均核验原 PDF SHA 后重新解析，分别使用新的空 provider state；没有读取旧引用阶段或人工补查结论，模型调用均为 0。

| 论文／轮次 | 完整提取 | 已核实／未核实 | 核实率 | 引用试验分 | 实际 HTTP（Crossref／arXiv API／publisher） | 耗时 |
|---|---:|---:|---:|---:|---|---:|
| LoRA v2 `lora-final-live` | 62/62 | 59／3 | 95.16% | 9.709677 | 61／48／10 | 198.6718 秒 |
| LoRA `lora-final-offline` | 62/62 | 59／3 | 95.16% | 9.709677 | 0／0／0 | 2.7701 秒 |
| CoT v5 `cot-final-live` | 30/30 | 28／2 | 93.33% | 9.6 | 30／21／10 | 100.9914 秒 |
| CoT `cot-final-offline` | 30/30 | 28／2 | 93.33% | 9.6 | 0／0／0 | 0.9326 秒 |

四轮均无 conflict、not_checked 或禁止 HTTP 尝试。LoRA 每轮 156 个来源、CoT 每轮 73 个来源均为 ok；在线／离线的全部 92 条输入、选择结果、理由、元数据、逐项分及核实率逐项一致。每轮另核对原文完整条目、元数据和新解析源内位置，以及 22 个实现文件摘要和未改变的 Rubric 字节；来源及实际证明链快照分别校验 312／146 个。开发原文清单来自独立于提取代码的原 PDF 整理，不作为正式人工 Gold 或普遍准确率。

剩余 5 条保留具体原因：LoRA `ref_020` WARP 的引用年份 2020 与官方 2021 日期不符；`ref_044 / ref_045` GPT1／GPT2 在当前有界自动发现范围内没有取得适当目标，不能据此声称官方文献不存在。CoT `ref_001` 为私人通信，`ref_005` 为尚未解释的署名差异。之前人工定位的 GPT 来源没有硬编码成自动规则。

全量自动化 **1,362 项通过**，保留 2 个已有依赖弃用警告；app／tests 及新验收脚本 Ruff 通过。新增核实率和解释字段未改 0.2 数值规则；此次只验收引用自动分支，不作为新的整篇 DeepSeek、六篇正式跨论文或前端 UI 验收。

详细结果见 `outputs/reference-auto-20261019/report.md`（本地运行产物，未随仓库发布），四轮汇总、来源与 HTTP 轨迹在同根目录的各 run 子目录，最终闭合复核为 [`final-artifact-audit.json`](../../../../outputs/reference-auto-20261019/final-artifact-audit.json)。早期 `lora-live/` 为修复前尝试，保留历史但不用于以上最终数值。此前 37 条定向人工补查继续单独留档，当前自动结果以本节及 final 产物为准。
