# 前端

[English](README.md) · **中文**

Paper Credibility Check 的 React + TypeScript + Vite 前端。界面语言是英文；后端返回的 `message`、`summary` 等文本原样显示，前端不翻译。

## 页面

一个顶栏，三个栏目：

- **Credibility check**（首页，`/#check`；旧地址 `/#credibility` 仍可用）：输入 arXiv ID、链接或 PDF 发起核查，可以勾选"同时做五维评分"。表单下方列出已完成核查的示例报告。
- **核查进行中**：页面说明预计等待时间，进度停在 60% 直到三项检查都结束。等待时可以阅读示例报告或五维快评分；切到别的栏目时轮询不停，顶栏的进度胶囊可以点回来。
- **报告页**：总分和三项检查（主张与证据、参考文献、代码与复现），证据在侧边抽屉里打开。**How this check ran** 展示每项检查的耗时与状态、模型步骤、引用 agent 核实的条数和代码搜索轨迹。这些信息随最终报告返回，不是实时推送。
- **Paper scoring**：五维评分流程，包括落地页、进度页和报告。
- **Reports**：所有已保存的可信度核查和五维评分，可按类型筛选、搜索。打开旧报告不会再次调用模型。

## 运行

需要 Node.js 20.19 及以上（或 22.12 及以上）和 npm 10 及以上。

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

开发服务器把 `/v1` 转发到 `http://127.0.0.1:8000`，所以要先启动后端（见 [backend/README.zh-CN.md](../backend/README.zh-CN.md)）。HTTP 模式下请求失败会显示错误，不会退回 Mock 数据。

如果模型下拉框是空的，检查后端 `GET /v1/models`。`is_available: false` 通常表示后端没读到 API key，改好 `.env` 后重启后端并刷新页面。

### Mock 模式（不需要后端）

```bash
VITE_API_MODE=mock npm run dev -- --host 127.0.0.1 --port 5174
```

Mock 模式不上传文件，也不调用模型、解析器或任何外部服务。

- **Paper scoring** 可以选四种结果：完整报告、含 `uncertain` 维度的临时报告、可重试的模型超时、不可重试的 PDF 解析失败。Mock API 会依次走过排队、七个处理阶段和结果。数据在 `public/mock-data.js`。
- **Credibility check** 提供几种审阅场景：一条预置主张（arXiv `2310.01798v1`，真实原文第 1 页和第 5 页）、核心矛盾、外部查询失败、代码不适用、解析失败。除第一条外都是合成数据，不会冒充对输入论文的解析结果；外部 `example.invalid` 链接标明为不可用的示例。

Mock 模式和后端的 fixture 模型都会把报告标成示例，避免把示例分数当成真实结论。

## 命令

```bash
npm run dev      # 开发服务器
npm test         # Vitest 测试
npm run lint     # oxlint 检查
npm run build    # 类型检查并生成生产构建
npm run preview  # 预览生产构建
```

## 代码位置

- `src/App.tsx`：顶栏、栏目切换和接口模式选择（`VITE_API_MODE`）。
- `src/types/assessment.ts`、`src/types/credibility.ts`：五维字段合同 v0.4 和可信度合同 `credibility-0.5.0-draft` 的类型。
- `src/api/`：每条流程一个接口，各有 HTTP 适配（`http*Api.ts`）和 Mock 实现（`mock*Api.ts`）。HTTP 适配负责上传、创建任务、带退避的轮询和历史列表。
- `src/hooks/useAssessment.ts`：五维评分的创建、轮询和重试。
- `src/utils/arxiv.ts`：arXiv 输入归一化，规则与后端 `app/parsing/arxiv.py` 一致。
- `src/constants/`：文案、错误码标题、五个维度和分数色带。
- `src/components/`：`CredibilityWorkspace`、`CredibilityReport`、`CredibilityProcess`（核查过程）、`CredibilityEvidenceDrawer`、`ExampleReports`、`ReportsPage`，以及五维的 `LandingPage`、`InputWorkspace`、`AssessmentProgress`、`AssessmentReport`、`EvidenceDrawer`、`AssessmentErrorView`。
- `src/index.css`：设计令牌和页面样式，不依赖 UI 框架。
- `src/test/fixtures/`：测试用的真实后端响应。

## 显示约定

- **无法判断不等于 0 分。** `score` 为 `null` 的维度显示 `—` 和原因，总分旁边标出实际参与计算的维度数。
- **每条结论都能回到来源。** 证据标签显示页码和位置（如 `Supports · p. 7 · Table 2`），点击打开抽屉，`←` / `→` 在同一结论的多条证据间切换，`Esc` 关闭。
- **分数由后端给出。** 总分、评级、分类、证据关系以及可选模型和会议都由后端计算，前端不重新计算。

## 部署

`/v1` 代理只在 `npm run dev` 时生效。生产构建或 `npm run preview` 需要另配反向代理，把 `/v1` 转发到后端。模型 API key 只能由后端读取，不要放进前端或任何 `VITE_` 变量。
