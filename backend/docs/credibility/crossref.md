# Crossref 引用核查运行手册

本手册用于运行、恢复和验收 Crossref 公开元数据查询。引用分支直接读取已解析论文，不调用大模型；实现覆盖请求调度、限流、成功响应缓存、引用切分和保守版本匹配，不修改公开评分规则或条目分类合同。完整任务的持久化与恢复见 [durable-evidence.md](durable-evidence.md)，0.2 试验评分见 [provisional-scoring.md](provisional-scoring.md)。

当前自动流程已在 Crossref 之后接入 arXiv 与官方出版来源补查，新增 v0.3 核实率／未核实原因摘要；操作见 [reference-auto.md](reference-auto.md)。本页后续各节保留当时的 Crossref 专项、切分修复和人工定向补查记录；其中“自动回退待接入”是历史阶段状态，不覆盖当前实现。

## 配置与共享状态

所有访问 Crossref 的后端 worker 必须使用同一个 `CROSSREF_STATE_PATH`。该路径是独立 SQLite 文件，与保存 assessment 的 `DATABASE_URL` 无关；修改任务数据库不会自动隔离或共享 Crossref 状态。默认文件为后端目录下的 `data/crossref/state.sqlite3`。

配置示例：

```dotenv
# 替换为本机所有 worker 可访问的同一个绝对路径。
CROSSREF_STATE_PATH=/absolute/path/backend/data/crossref/state.sqlite3
CROSSREF_MIN_INTERVAL_SECONDS=1
CROSSREF_TIMEOUT_SECONDS=15
CROSSREF_CACHE_TTL_SECONDS=604800
CROSSREF_BRANCH_DEADLINE_SECONDS=600
CREDIBILITY_CROSSREF_WAIT_BUDGET_SECONDS=60

# 仅为虚构占位地址；实际联系地址在本地配置，不写入仓库。
CROSSREF_MAILTO=project-contact@example.invalid
```

联系邮箱以 `SecretStr` 读取，仅用于 HTTP `User-Agent` 联系标识，不进入请求 URL、缓存 key、公开报告或请求 fingerprint。邮箱可留空；不要把邮箱拼入查询字符串。

共享 SQLite 保存 FIFO 队列、一个有效请求 reservation、全局请求间隔与冷却时间，以及成功响应缓存。不同进程查询同一规范 key 时共享队列位置；成功响应提交后，等待者读取缓存，不再重复 HTTP。普通间隔默认至少 1 秒，服务端限流头可进一步放慢共享节奏。网络请求和等待均在数据库事务之外执行。

SQLite 共享路径适用于同机 worker。多主机或各自使用独立磁盘的实例需要统一 Crossref 调度服务；给每个实例配置同名字但不同物理文件，不能形成全局限流。不要把网络文件系统上的 SQLite 当作已经验证的多主机调度方案。

## 查询、证据与失败恢复

成功缓存默认保留 7 天，使用持久化 UTC 时间判断过期。缓存命中先于冷却和排队判断，因此其他查询遇到 429 时，已缓存的查询仍能完成。只有成功 HTTP 200 响应进入成功缓存，失败不会覆盖已有有效成功记录。

缓存保存规范 JSON、可用的原始响应字节及各自摘要。读取时校验 JSON、字节摘要及二者的一致性；损坏记录不能作为成功证据使用。若记录只有规范 JSON，会明确标记 `normalized_only`，不能宣称它包含原始 HTTP 字节。

每次真实响应和缓存读取都有独立的不可变观察快照。相同 artifactroot 内可关联原快照；换到另一个 artifactroot 时，从经过校验的缓存内容创建本地快照，不假定旧快照文件存在于新目录。公开 source 的状态必须与本地快照一致。失败 attempt 留在审计快照链中，成功补查使用本轮有效 source；历史 429 不应被重新当作当前结果的失败来源。

429 优先遵守 `Retry-After` 的秒数或日期；缺失、无效时默认冷却 30 秒，不提前缩短服务端要求的等待。同一条查询最多执行一次 429 后补查。整篇引用分支共享两个界限：

- 默认 60 秒的累计冷却等待预算，所有条目共同消耗，不按条目重新计时。
- 默认 600 秒的分支 deadline，覆盖排队、正常间隔、冷却及请求执行。取得 SQLite reservation 后重算剩余时间；实际请求 timeout 不超过该值。HTTP 重定向和逐块读取共用同一 monotonic 请求预算，不会每次重置。

普通 FIFO 排队和请求间隔不消耗 60 秒冷却预算，但消耗分支 deadline。无法继续等待时，保留真实 `rate_limited` 或 `timeout` 状态，并注明没有发送请求的等待观察。重启和 `clear_failed_cache()` 均不会清除共享冷却时间。

进程中断后，已经提交的成功缓存可供下一进程复用；中断时尚未提交的外部请求可能再次执行，不保证 exactly-once。恢复时保留同一 state 文件，换一个输出目录重新运行下面的脚本。不要通过删除 state 文件来绕过服务端冷却。若需要回退代码或处理状态文件故障，先停止相关 worker 并保留状态文件、输出和快照，避免新旧调度方式同时向服务发请求。

## HTTP 边界

生产路径使用固定 `https://api.crossref.org` 的 `/works` 查询和 DOI 查询端点，通过 `httpx.Client` 的有界流式读取限制响应体，默认最多 2,000,000 字节；不使用环境代理配置。URL 和重定向均经过同一 Crossref 主机及路径校验，拒绝跨主机跳转和携带联系邮箱的 URL。无效 JSON、无效 Crossref message、超限响应和传输异常不会成为成功缓存。

独立验收脚本在 live 模式保持此生产流式路径，并在 `Client.send` 处计实际 HTTP 次数；不能把顶层 `httpx.get` 调用次数当成生产 HTTP 次数。offline 模式同时禁止 getter 和 send，并在执行后检查禁止调用计数，即使传输层捕获了异常，也不能据此给出通过。

## 独立真实验收

从后端目录执行。需要现有 `.venv`、下面旧任务的 parsing checkpoint 及其完整快照目录。脚本只读 parsing 输入和任务 fingerprint，不读取旧 assessment 报告或旧 reference 结果来填充新结果。

首次运行会访问真实 Crossref，不调用任何模型。所有新状态、结果和快照写入 `outputs/crossref-hardening-20261019`；不覆盖 `be07` 历史报告：

```bash
.venv/bin/python scripts/verify_crossref_live.py \
  --input-db ../../outputs/be07-implementation-20261019/native-final/tasks.db \
  --input-artifacts ../../outputs/be07-implementation-20261019/native-final/artifacts \
  --assessment-id cred_842d92efbadf426dbcce36238d469afa \
  --state-path ../../outputs/crossref-hardening-20261019/state.sqlite3 \
  --output-dir ../../outputs/crossref-hardening-20261019/live-01 \
  --expect-entries 60
```

第一进程结束后，启动新的进程，复用同一 state 文件、使用新的输出目录。默认 artifactroot 随输出目录改变，因此同时验证跨进程和跨 artifactroot 缓存复用：

```bash
.venv/bin/python scripts/verify_crossref_live.py \
  --input-db ../../outputs/be07-implementation-20261019/native-final/tasks.db \
  --input-artifacts ../../outputs/be07-implementation-20261019/native-final/artifacts \
  --assessment-id cred_842d92efbadf426dbcce36238d469afa \
  --state-path ../../outputs/crossref-hardening-20261019/state.sqlite3 \
  --output-dir ../../outputs/crossref-hardening-20261019/cache-02 \
  --expect-entries 60 \
  --offline-cache
```

输出目录必须尚不存在；再次运行时换用新的目录后缀，不删除或覆盖上一轮。脚本不设置 `previous_check`，也不加载完成的 reference 阶段，因此第二条命令验证的是查询缓存，而非保存好的引用结论。

每轮输出：

- `input-context.json`：本轮执行输入；来源 fingerprint 和 parsing 快照信息记录在 `summary.json`。
- `reference-result.json`、`sources.json`：本轮引用分支结果及有效来源。
- `http-audit.json`：实际 HTTP、禁止请求、lookup 和缓存命中的逐项记录。
- `summary.json`：计数、快照校验、状态、脚本摘要及耗时。
- `artifacts/`：本轮输入、结果和 source 的不可变快照。

完整查询结束且没有 `not_checked` 时退出码为 `0`；仍有未完成项时为 `2`；离线 cache miss、快照校验或其他断言失败时为 `1`。首次真实运行若遇到 429，不应为了获得通过数字放宽状态或缩短冷却；等待服务恢复后，在新的目录进行 live 补查，再执行新的 offline 轮次。

offline 验收要求 getter、实际 HTTP 和禁止请求计数均为零，缓存命中与实际 lookup 数相符，所有 source 快照均可在本轮 artifactroot 校验。`--expect-entries 60` 固定本轮解析出的条目集合，不表示这些条目的拆分或匹配结论已经过人工 gold 标注。

## 分类与覆盖范围

本次保留现有公开分类和 0.2 评分规则。DOI 查询视图会规范化 PDF 的兼容连字；仅补接 ACM 明确的 7 位＋7 位数字 DOI 空白，DOI-only 标识不作为论文标题。原始条目和 fragments 不改写。`reference-hints-1.1-doi-typography` 已进入任务 fingerprint；旧任务跨版本恢复会被拒绝，应新建任务，历史报告不重写。成功完成元数据查询但没有可信匹配时为 `unverified`；这不证明引用虚构。404 目前保守保留为 source `not_found`、item `not_checked`，会限制分支完成度；404 与 provider 范围不足的业务分类需在后续合同任务中统一处理，本轮不改为成功。

Crossref 分支能够提供 DOI、候选标题和可用年份的公开元数据核查，以及明显 DOI 与标题冲突的证据。作者现用于查询和保守兼容性校验，姓名空白、连字符和重音排版可归一化；真实署名差异需要独立证据。DOI 存在性匹配不等于每个作者字段均完全一致。Crossref 不能覆盖全部 arXiv-only 条目、所有论文版本或全部非 DOI 文献，也不能证明正文的引用内容支持论文主张。没有独立人工标注和完整类别评测前，不报告全量准确率。

## 真实验收记录

本地证据位于工作区 `outputs/crossref-hardening-20261019/`。LoRA 各轮只读取同一固定解析输入（62 blocks）；CoT 使用原固定解析输入。均不读取旧 reference 结果、不设置 `previous_check`，也不调用模型。

| 轮次 | 实际 HTTP / 缓存命中 | 结果 | 耗时 |
| --- | --- | --- | --- |
| `live-01` 首次真实查询 | 59 / 1 | 59 checked，18 matched、1 metadata_conflict、40 unverified、1 not_checked；0 个 429 | 68.86 秒 |
| `live-03` DOI 修复后新执行 | 2 / 58 | 60 checked，20 matched、40 unverified、0 conflict、0 not_checked；60 个 source 均为 HTTP 200 | 2.04 秒 |
| `cache-02` LoRA 独立进程、独立 artifactroot、禁网络 | 0 / 60 | 60 checked；getter、send、blocked 计数均为 0；60 个来源快照全部校验通过 | 0.39 秒 |
| `cot-live-04` CoT 论文固定解析输入 | 30 / 0 | 30 checked，13 matched、17 unverified、0 conflict、0 not_checked；30 个来源均为 HTTP 200 | 34.25 秒 |
| `cot-cache-05` CoT 独立进程、独立 artifactroot、禁网络 | 0 / 30 | 30 checked，0 个禁止请求；30 个来源快照全部校验通过 | 0.18 秒 |

`live-01` 的 60 个条目只有 59 个规范 query key，因此有一次同轮缓存命中。唯一 404 来自 ref_034 的 PDF 连字导致 DOI 截断；ref_019 的 ACM DOI 被空白截断并误查会议目录，产生疑似冲突。保守修正 DOI hint 后，两条新 DOI 均重新真实请求，旧 404／疑似冲突记录保留在 `live-01`，不将其认定为论文负面发现。其余成功响应从经过校验的持久缓存复用，逐条重新匹配。

这 60 个条目是固定提取集合，不是人工核定的 bibliography 总数；40 个 `unverified` 表示当前元数据／匹配规则仍未确认，不等于引用虚构。未将此次引用专项计作新的整篇 DeepSeek 或正式人工验收。

联系标识实测返回 `x-api-pool=polite-single`、HTTP 200，响应头显示 rate 10 / 1s、concurrency 3；实现继续使用更保守的每秒至少 1 次和全局单并发。邮箱不写入证据文件。官方访问通道说明见 [Crossref access and authentication](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)。

全量自动化为 **903 passed**（真实网络在 pytest 中被禁用，使用受控替身），`make lint` 及 scripts Ruff 通过。自动化另验证四进程同 key 去重、单并发、429 恢复、过期 lease 拒写、缓存损坏拒用、总截止时间及超时 429 保留冷却头。真实记录和源码摘要见各目录的 `summary.json`、`http-audit.json` 与 `final-source-manifest.json`。

两篇的 90 个来源在各自新进程、不同 artifactroot 下全部命中查询缓存，0 HTTP；新建 checks 与 evidence 内容完全一致。汇总见 `verification-summary.json`。这说明本次两个固定提取集合的查询机制完成，不代表所有论文的提取／匹配准确率。

## 未核实条目查因补充（2026-10-19）

用户要求先查清 LoRA 40 项与 CoT 17 项的原因。57 项全部完成原文／候选／定向查询审查，应用和历史报告未修改。按主因互斥归类：LoRA 为 11 解析／切分、8 匹配／版本规则、21 当前 Crossref 查询未召回；CoT 为 1 解析、3 匹配／版本规则、12 未召回、1 私人通信。14 个提取项的目标记录已在原候选中，另 2 项通过更正输入／按已知 DOI 取回，故至少 16 项不能归作“Crossref 未收录”；其中包含重复碎片，不能当作独立论文数或新增 matched 数。33 个未召回只能指本次有界查询结果，不能证明全库不存在其他版本。

原固定 LoRA v2 PDF 第13–16页复核有62条引用（含重复版本）。当前提取60项来自 `62 - 5条被合并 + 3个尾部伪条目`；所以本手册前面的60/60代表固定提取项查询完成，不代表原文引用完整覆盖。需先修切分和分母，再处理年份／题名／版本关系及 arXiv／DataCite／出版源回退。本轮未变更判定、评分和旧报告，也不将查因当作修复完成。

完整57项证据、69个已校验补充查询快照及原文62条映射保存于工作区 `outputs/reference-unverified-audit-20261019/diagnosis.md`、`audit-items.json`、`audit-items.csv`、`audit-summary.json` 和 `lora-original-bibliography-map.json`。


## 切分、解析与匹配修复后的真实验收（2026-10-19）

本节是当前实现状态，前面的 60/40 与 30/17 为修复前记录。LoRA 固定原 PDF 重新解析后完整提取 **62/62**，逐条比对独立于提取代码整理的原文清单，包含每条完整字符、边界、标题／作者／年份／标识符和源内锚点；CoT 同样为 **30/30**。没有以强制条目数或删减引用的方式凑分母。原始 fragments 保留原文，归一化只用于提示与身份比较。这是开发验收，不代替人工 Gold 或全量准确率评测。

修复包括长作者列表和无年份引用的新条目边界、跨页出版页眉、场所尾部伪条目、跨行 DOI/arXiv、首作者污染、PDF 连字／空白／重音及完整姓名排版。原文明确 `et al.` 时仅允许已列作者为完整外部名单的严格子集；正式记录与官方预印本的完整外部名单仍逐一兼容。不同给名不自动当作别名。

公开分类 0.1／0.2 和试验评分数值规则保留。内部版本 `reference-hints-1.2-boundaries-authors`、`reference-matching-1.1`、`reference-versions-1.1` 已进入 request fingerprint，防止旧阶段结论混入新实现。不同年份的同题关系需相同完整外部作者及官方日期支持，证据明确标作 `dated-preprint-metadata` inference；改题必须有官方 DOI 与旧版本原题，若引用年份落在相邻版本改题年，则保存 `cited_title_valid_year_range`、`date_inference` 和实际版本记录。WARP 早于官方首发的 2020 年不因此放行，2025 年同题 posted-content 也不能替代 Attention 的 2017 年原作。

Crossref 仍提供匹配候选；只有需要解释版本且已有兼容正式候选时才补查固定 `https://export.arxiv.org/api/query`。arXiv 不调用模型、不遍历任意网页，单并发、间隔至少 3 秒，最多 5 条／512 KB、10 秒 HTTP 预算，遵守 429 冷却。XML 原始响应和规范化记录进入不可变快照，成功 XML 以校验 envelope 缓存，独立状态文件为 `CROSSREF_STATE_PATH + ".arxiv-versions.sqlite3"`。版本来源失败会保留 `unverified + partial`，不会报完整完成。

| 固定论文 | 修复前提取／已核实／未核实 | 本轮提取 | 已核实 | 未核实 | 查询失败／未检查 |
|---|---|---:|---:|---:|---:|
| LoRA v2 | 60 / 20 / 40 | 62 | 32 | 30 | 0 |
| CoT v5 | 30 / 13 / 17 | 30 | 16 | 14 | 0 |

最终轮使用新的空 `final-state.sqlite3`：LoRA 发出 Crossref 61 次和 arXiv 11 次，1 次相同查询在本轮内复用缓存；CoT 发出 Crossref 30 次和 arXiv 2 次。82 + 33 个来源快照全部有效；每篇再以新进程禁止联网重跑，62／30 次 Crossref 查询缓存命中、HTTP 0 次，逐条输入／候选选择／结果相同。两篇没有使用旧引用阶段、没有模型调用，也未覆盖历史报告。全套 **1,176 项测试通过，ruff 通过**。

LoRA 剩余 30 项为：25 项当前查询未取得可确认目标，2 项同作者异题缺少关系证据，1 项同题不同作者，1 项原文年份矛盾，1 项只召回后来的 posted-content。CoT 剩余 14 项为：12 项当前查询未取得目标，1 项私人通信，1 项真实署名差异缺少别名／更名证据。未召回不等于 Crossref 全库未收录。CoT 原已核实的署名差异项改为保守未核实，不能仅看计数增长判断质量。

脚本新增 `--input-pdf`：仍只读原 parsing checkpoint 验证任务 lineage，但核对原 PDF SHA 后用当前 GenericPdfParser 重解析；CoT 不再依赖有黏连词的旧 benchmark blocks。真实/缓存重跑命令均加此参数，分别使用新的 `--output-dir`；第二次另加 `--offline-cache`。执行示例：

```bash
.venv/bin/python scripts/verify_crossref_live.py \
  --input-db ../../outputs/be07-implementation-20261019/native-final/tasks.db \
  --input-artifacts ../../outputs/be07-implementation-20261019/native-final/artifacts \
  --assessment-id cred_842d92efbadf426dbcce36238d469afa \
  --input-pdf ../../outputs/be07-implementation-20261019/native-final/arxiv/2106.09685v2.pdf \
  --state-path ../../outputs/reference-fix-20261019/new-check-state.sqlite3 \
  --output-dir ../../outputs/reference-fix-20261019/new-lora-live \
  --expect-entries 62
```

CoT 使用 `cred_84983f4a99e24180bc4d9b5b93303aaa`、`../../tmp/pdfs/pilot-selection/paper_005.pdf` 和 `--expect-entries 30`。脚本核 SHA，不能以其他 PDF 替代。同一次真实验收与缓存复跑共用 state；重新证明冷缓存真实查询需换尚不存在的 state 路径。

详细结果与 44 项剩余清单见 `outputs/reference-fix-20261019/report.md`（本地运行产物，未随仓库发布），逐条原文验收为 `final-extraction-acceptance.json`，真实运行 `lora-final-live/`／`cot-final-live/`，禁止联网复跑 `lora-final-cache/`／`cot-final-cache/`，汇总为 `final-verification.json`。本轮只完成引用专项，不据此标记整体正式验收通过。


## 多来源定向补查结果（2026-10-19，37 条）

对上一轮 `target_not_retrieved` 的 LoRA 25 条、CoT 12 条补读新获取的官方来源，**37/37 均取得书目身份及可解释年份／版本依据**。来源包括 arXiv 固定版本、PMLR、ACL Anthology、NeurIPS、JMLR、ICLR 官方节目页和 OpenAI 原报告 PDF。合并审阅报告为 LoRA **57/62 已核实、5 未核实**，CoT **28/30 已核实、2 未核实**。剩余 7 条不在本轮 37 条子集中，保留原判定，本轮未重新审查。

32 个新原始来源快照逐一校验，其中 27 个可用官方快照支撑确认；OpenReview 的 4 次 403 及 1 个浏览器验证页保留为不可用尝试，用其他官方来源补证，未当作成功。37 条涉及 36 个作品，Decoupled 2017／2019 两条为同一 arXiv 作品。其 2017 条目保留“初稿年＋后续题名”的版本注记；Adam 2017 等引用依据真实修订版日期，不能说成首次发表年份；GPT1／GPT2 原引无年份，继续为 `null`。

这是 agent 辅助的定向补查，**尚未接入后端自动多来源回退**。实现源码、旧 API／历史报告和评分规则均未改。另存的两个 `*-reviewed-reference-result.json` 使用既有 `ReferenceCheckResult` schema，原始条目与 `parsed` 保留，并新增来源、匹配候选及可回查的审阅依据；152 个合并报告来源快照全部校验，离线验证禁止一切 HTTP。

逐条链接、版本说明和 7 条保留项见 `outputs/reference-multisource-20261019/report.md`（本地运行产物，未随仓库发布）；`all37-evidence.json/csv` 为原始审阅明细，`supplement-manifest.json` 为 hash／schema／来源及自动接入边界记录。后端目录执行 `.venv/bin/python ../../outputs/reference-multisource-20261019/verify_supplement.py` 可离线复核。

接续实现时，已有 arXiv ID 优先读取指定记录和版本；否则使用官方论文集／出版社 metadata，搜索仅作入口发现。将 URL 白名单、原始快照、全作者／标题／版本核对、供应商限流与缓存、失败回退和查询级验收一并接入，不能把本轮人工定位的 URL 清单硬编码成只对两篇论文有效的规则。
