# 前端

[English](README.md) · **中文**

PaperTrace 的网页前端：React 19、TypeScript、Vite、Tailwind CSS 4、TanStack Router 与 Query，以及 pdf.js。界面语言是英文。

## 页面

- **开始页**（`/`）：选择 arXiv 或 PDF，选择模型（GPT、Claude、Gemini 或 DeepSeek，以后端实际提供的为准）。勾选 "Include a five-dimension quality assessment" 时，同一次运行会并行做五维质量评估。
- **等待页**：进度表盘、每项检查当前在做什么和预计耗时，以及五维评估自己的进度。跑得特别久的单项检查可以用 "Stop check" 停掉，整次运行可以用 "Cancel assessment" 取消。每次运行在标题栏都有一个标签页，离开后可以随时回来。
- **运行页**（`/check/$id`）：顶部两个开关，一个选择部分（**Credibility** 或 **Quality**），另一个选择可信度报告的看法。
  - **Paper** 视图：论文 PDF 居中，证据直接在原文上高亮。左栏有 Outline、Pages 和 Skills（高亮图层）；右侧组件区有 Verdict、Claims、References、Code、Evidence、Process record 和 Notes，可以折叠、拖动排序、关闭后再用 "+" 加回来。
  - **Report** 视图：可信度报告以文档形式呈现，左侧是章节目录。
  - **Quality**：五维质量报告。五维失败或没跑时，可以在这里重试或补跑。
- **History**（`/reports`）：每次运行一行，同时列出两部分结果，支持搜索、重试和删除；正在运行的排在最上面。
- **导出**："Report (PDF)" 打开包含两部分的打印页（`/check/$id/print`）；"Raw data (JSON)" 下载这次运行的两份原始数据。每份报告都有自己的地址。
- **帮助助手**（可选）：右下角的对话窗，回答怎么使用 PaperTrace、怎么看报告。

笔记和"已复核"标记只保存在浏览器里。上传的 PDF 存在浏览器的 IndexedDB 中，刷新后报告仍能显示原文。

## 运行

需要 Node.js 20.19 及以上（或 22.12 及以上）和 npm 10 及以上。

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

开发服务器把 `/v1` 转发到后端 `http://127.0.0.1:8000`（见 [backend/README.zh-CN.md](../backend/README.zh-CN.md)），把 `/assistant` 转发到帮助助手 `http://127.0.0.1:8020`。请求失败时会显示错误，不会退回示例数据。

如果模型下拉框是空的，检查后端 `GET /v1/models`。`is_available: false` 通常表示后端没读到对应模型的 API key，改好 `backend/.env` 后重启后端并刷新页面。

### Mock 模式（不需要后端）

```bash
VITE_API_MODE=mock npm run dev -- --host 127.0.0.1 --port 5174
```

Mock 模式使用 `public/mock-data.js` 和 `src/api/mock*Api.ts` 里的示例数据，不上传文件，也不调用模型、解析器或任何外部服务，报告会标明是示例。

### 帮助助手（可选）

```bash
npm run assistant          # 在 8020 端口启动 assistant/server.mjs
npm run assistant:check    # 核对 guide.md 与界面是否一致
```

帮助助手是一个无第三方依赖的 Node 小服务。它把 `assistant/guide.md` 和当前页面的简短摘要（分数和计数，不含论文全文和笔记）发给任意 OpenAI 兼容的对话接口。它读取 `ASSISTANT_API_KEY`、`ASSISTANT_BASE_URL`、`ASSISTANT_MODEL`，没有设置时沿用 `../backend/.env` 里的 `OPENAI_*`。不启动时，对话窗会提示暂不可用，其他功能不受影响。指南的维护方法见 [assistant/README.md](assistant/README.md)。

## 命令

```bash
npm run dev              # 开发服务器
npm test                 # Vitest 测试
npm run lint             # oxlint 检查
npm run build            # 类型检查并生成生产构建
npm run preview          # 预览生产构建
npm run assistant        # 帮助助手服务
npm run assistant:check  # 静态检查助手指南
```

## 代码位置

- `src/main.tsx`、`src/router.tsx`：入口和路由（`/`、`/check/$id`、`/check/$id/print`、`/score/$id`、`/reports`）。
- `src/app/`：外壳和标题栏标签页、接口模式选择（`VITE_API_MODE`）、数据查询、可信度核查与五维评估的配对、删除运行、PDF 存储。
- `src/features/home/`：开始页。
- `src/features/check/`：等待页、实时进度和两部分并列的运行页。
- `src/features/report/`：PDF 阅读器、报告文档、组件区、目录和打印版报告。
- `src/features/score/`：五维质量报告。
- `src/features/reports/`：历史列表。
- `src/features/assistant/`：帮助助手对话窗。
- `src/ui/`：共用控件（按钮、标签、进度表盘、分数仪表、模型选择、组件框、图标、动效）。
- `src/api/`：两类评估的 HTTP 适配（`http*Api.ts`）和 Mock 实现（`mock*Api.ts`）。
- `src/types/`：字段合同 v0.4 和可信度合同 `credibility-0.5.0-draft` 的类型。
- `src/styles/`：设计令牌（`theme.css`）和组件样式，规则见 [docs/design/DESIGN_SYSTEM.zh-CN.md](docs/design/DESIGN_SYSTEM.zh-CN.md)。
- `src/utils/arxiv.ts`：arXiv 输入归一化，规则与后端 `app/parsing/arxiv.py` 一致。
- `public/brand/`：PaperTrace 的 logo 和图标。
- `assistant/`：帮助助手服务、指南和检查脚本。
- `src/components/`、`src/App.tsx`、`src/hooks/`：早期单页界面，连同测试一起保留。

## 显示约定

- **无法判断不等于 0 分。** 没有分数的维度会说明原因。没能完成的检查得固定兜底分 3，并明确标出，不会当成发现呈现。
- **每条结论都能回到来源。** 高亮和证据标签都能打开背后的原文段落、参考文献或代码来源。
- **分数由后端给出。** 分数、类别、证据关系和可选模型都由后端计算，前端不重新计算。

## 部署

`/v1` 和 `/assistant` 的代理只在 `npm run dev` 时生效。生产构建或 `npm run preview` 需要为这两个路径另配反向代理。模型 API key 只能由后端和助手服务读取，不要放进前端或任何 `VITE_` 变量。
