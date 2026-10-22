# 可信度任务持久化、恢复与证据重放

可信度任务使用现有 `DATABASE_URL` 的 SQLAlchemy 连接，独立新增 `credibility_tasks` 和
`credibility_stage_checkpoints` 两张表。旧五维任务及历史表不改结构。
`_TASKS` 只保留兼容缓存；GET 从数据库读取，服务重启后仍可查询已保存任务。

## 启动和配置

继续使用已有 `.env`，不要把密钥写进命令或版本库。例如：

```sh
LLM_PROVIDER=deepseek PARSER_PROVIDER=generic CREDIBILITY_WORKERS=3 make run-generic
```

| 配置 | 默认值 | 含义 |
|---|---|---|
| `DATABASE_URL` | 原有配置 | 任务与阶段记录；恢复时必须指向同一数据库 |
| `CREDIBILITY_ARTIFACT_DIR` | `UPLOAD_DIR` 父目录下 `credibility-artifacts/` | 内容寻址的不可变来源、模型输入与阶段产物 |
| `CREDIBILITY_WORKERS` | 3，范围 1～3 | 解析完成后三项检查的并发上限；设 1 可做串行对照 |
| `CREDIBILITY_LEASE_SECONDS` | 1800 | 执行租约；运行中定期续租，旧执行者失去租约后无法写回 |

Crossref 的共享状态、限流与缓存配置见 [crossref.md](crossref.md)。模型外部核查及工具预算见 [external-tools.md](external-tools.md)。启动不会自动运行付费能力探测。

解析产物保存后，主张、引用和代码分支独立执行。单个分支失败时，其他分支继续执行并保存；
能够形成有效报告时，任务返回 `completed`，失败分项保留 `check_status=failed` 和执行错误。
无法解析论文或无法组装有效报告时，任务仍为 `failed`。`completed` 不等于所有来源均已核实。

## 查询、恢复与重放

以下命令均从 backend 根目录执行，并使用与原任务相同的数据库、产物目录和模型配置：

```sh
.venv/bin/python scripts/credibility_tasks.py inspect ASSESSMENT_ID --output /tmp/task.json
.venv/bin/python scripts/credibility_tasks.py list
.venv/bin/python scripts/credibility_tasks.py resume ASSESSMENT_ID --refresh-failures --output /tmp/resumed.json
.venv/bin/python scripts/credibility_tasks.py replay ASSESSMENT_ID --output /tmp/replay.json
```

`resume` 是显式重新执行操作，可能调用模型或联网。它校验源输入、模型／Prompt、解析配置、
流水线和 Rubric 内容指纹，复用已经校验并持久化的阶段，重新执行失败或未保存阶段。
主张抽取和证据判断另有独立检查点，抽取完成后判断失败，不需要再次抽取。
配置或规则变化时拒绝套用旧任务，应新建任务。

包含失败／部分执行分支的 `completed` 任务可显式恢复；完全成功的终态任务不能被恢复命令覆写。
新建 POST 始终分配新任务身份，不借用其他任务的检查结论。
GET 轮询、inspect 和 replay 都不会启动检查。

仍有未过期租约的任务默认拒绝接管。确认旧进程已经停止后，才可显式加入 `--force`：

```sh
.venv/bin/python scripts/credibility_tasks.py resume ASSESSMENT_ID --force --output /tmp/resumed.json
```

接管后 attempt 和 token 更新，旧执行者不能提交结果。外部模型请求已经发出、进程在响应落盘前中断时，
该响应无法安全复用；重试可能产生新的供应商调用，系统不承诺第三方恰好调用一次。

## 快照与离线重放

来源的 `snapshot_ref` 是 `snapshot:v1:<sha256>`，不是公开的本机文件路径。
原始响应、规范化内容和观察记录分别寻址；相同正文可去重，每次访问仍保留独立时间、状态和缓存标记。
写入先完成临时文件并原子落盘，再生成引用。模型输入保存实际发送的片段及截断信息；
思考模式所需的供应商内部推理字段不写入公开轨迹。

`replay` 读取当前保存报告，校验证据／来源 ID、快照内容哈希、来源 URL／状态身份和工具审计引用，
再用相同 Rubric 重算三项分数、总分和类别并核对。此操作的模型及网络调用次数为 0。
它不是重新核查网页，也不产生一份新的真实模型结果。快照缺失、篡改或计分不一致会拒绝重放。

重新调用模型分析固定快照和重新联网全流程运行均是新分析，必须单独留档，不能混为离线重放。

## 报告新增解释字段

HTTP 调用需要在创建请求中指定 `schema_version="credibility-0.2.0-draft"`，才会返回本节新增字段。
显式请求 `credibility-0.1.0-draft` 时保持旧报告形状，不向旧版 HTTP 响应追加这三个字段；
数据库仍保存运行所需的内部执行信息，命令行 inspect/replay 可据此检查和恢复任务。
`schema_version` 是接口合同版本，与 `rubric_version="0.2-provisional-complete"` 的计分版本独立，
新合同不会改变原有三项试验分和总分计算规则。

- `coverage`：按维度分别报告数量、统计范围、未知项、来源失败和计分回退规则。
  主张的分母是所选主张，引用的分母是已提取条目，不能据此声明论文全文已经覆盖。
  仓库搜索总体未知时，`total_count/completion_ratio` 保持 null。
- `execution`：实际并发上限，以及各分支是否完成、部分完成、失败或复用了检查点。
- `external_review`：实际模型外部核查方式、状态、审计快照和限制；可能为 null。

这些字段解释核查质量，不新增一个模型自报的可信度百分比。计分仍使用既有 0.2 试验规则；
同一组结构化检查结果的分数不变，技术失败和论文负面发现分别呈现。

## 数据结构与回退

新表按需创建，不迁移旧五维表，也不要求先部署额外队列或微服务。备份数据库和整个产物目录时应保持对应关系。
回退应用代码前停止新任务执行并保留备份；旧版本不会读取新表，新表可保留供恢复。
不把删除新表作为日常回退步骤，避免丢失已保存任务与证据索引。

验证命令：

```sh
.venv/bin/python -m pytest -q tests/test_credibility_task_storage.py tests/test_credibility_durable_pipeline.py tests/test_credibility_snapshots.py
.venv/bin/ruff check app scripts tests
```

真实多论文技术验收使用 [verify_credibility_suite.py](../../scripts/verify_credibility_suite.py)。
候选材料中的 AI 辅助预期与正式人工验收分别标记。
