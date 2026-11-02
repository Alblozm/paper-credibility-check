# 模型配置

[English](models.md) · **中文**

PaperTrace 每次运行都使用开始页 **Model** 菜单里选中的模型。后端支持四家：OpenAI（GPT）、Anthropic（Claude）、Google（Gemini）和 DeepSeek。想用哪家，就填哪家你自己的官方 API key。只配一家就能用；配了多家，用户每次运行时可以自己选。

所有设置都写在 `backend/.env`。第一次先从示例复制一份，之后每次修改都要重启后端：

```bash
cd backend
cp .env.example .env
```

## 1. 打开真实解析

示例 `.env` 默认是演示模式（`LLM_PROVIDER=fixture`、`PARSER_PROVIDER=stub`），只返回示例报告。要做真实核查，改成：

```ini
PARSER_PROVIDER=generic   # 解析任意 arXiv 论文或上传的 PDF
LLM_PROVIDER=openai       # 页面默认选中的模型：openai 或 deepseek
```

`LLM_PROVIDER` 只决定打开页面时默认选中哪个模型。如果这家没有配置 key，页面会自动选中第一个可用的模型，所以只用 Claude 或只用 Gemini 时，保持 `LLM_PROVIDER=openai` 即可。配置了真实 key 后不要再留在 `fixture`，否则报告会被标成演示输出。

用 `make run` 启动后端。`make run-openai` 和 `make run-deepseek` 是在命令行里设好同样两个值，分别默认选中 GPT 或 DeepSeek。

## 2. 为每家要用的模型填 key

| 厂商 | 申请 key | 在 `backend/.env` 里设置 | 默认模型（用哪个变量改） |
|---|---|---|---|
| OpenAI（GPT） | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | `OPENAI_API_KEY` | `gpt-5.6-terra`（`OPENAI_MODEL`） |
| Anthropic（Claude） | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) | `ANTHROPIC_API_KEY` 和 `CLAUDE_ENABLED=true` | `claude-sonnet-5`（`ANTHROPIC_MODEL`） |
| Google（Gemini） | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` 和 `GEMINI_ENABLED=true` | `gemini-3.7-flash`（`GEMINI_MODEL`） |
| DeepSeek | [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys) | `DEEPSEEK_API_KEY` | `deepseek-flash`（`DEEPSEEK_MODEL`） |

四家都配的示例：

```ini
PARSER_PROVIDER=generic
LLM_PROVIDER=openai

OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_ENABLED=true
GEMINI_API_KEY=AIza...
GEMINI_ENABLED=true
DEEPSEEK_API_KEY=sk-...
```

重启后打开 `http://127.0.0.1:8000/v1/models`，你配置过的每个模型都应该显示 `"is_available": true`。

## 注意事项

- **Claude 和 Gemini 要设两项。** 只填 key 时，模型会出现在菜单里但是灰的（`is_available: false`）；再设 `CLAUDE_ENABLED=true` 或 `GEMINI_ENABLED=true` 才能选。这个开关的用意是：先填 key、跑一篇样例确认没问题，再开放给用户选。
- **代码检查的联网搜索走 OpenAI 或 DeepSeek。** GPT、Claude、Gemini 都通过 OpenAI 的搜索查网页，DeepSeek 用它自己的搜索。没有 `OPENAI_API_KEY` 时，Claude 或 Gemini 的运行会跳过这部分联网搜索，报告里记为 disabled；代码检查的其余部分照常进行，包括论文里印出的链接和 GitHub 查询。所以即使主要用 Claude 或 Gemini，也建议配一个 OpenAI key。
- **一次运行只用一个模型。** 同一次运行里的五维质量评估和可信度核查用的是同一个模型。
- **接口地址。** Claude 和 Gemini 通过各家官方的 OpenAI 兼容接口调用。`ANTHROPIC_BASE_URL`、`GEMINI_BASE_URL`、`OPENAI_BASE_URL`、`DEEPSEEK_BASE_URL` 只有在走兼容代理时才需要改。
- **隐藏 DeepSeek。** 设 `DEEPSEEK_ENABLED=false` 后，即使填了 key 也不会出现在菜单里。
- **key 只放在服务器上。** 不要把 key 放进前端或任何 `VITE_` 变量，也不要提交 `.env`。

## 可选的 key

| 设置 | 作用 |
|---|---|
| `GITHUB_TOKEN` | 提高代码检查访问 GitHub 的限流额度，不需要额外权限的 token 就够。 |
| `CROSSREF_MAILTO` | 发给 Crossref 的联系邮箱，Crossref 建议 API 使用者提供。 |
| `ASSISTANT_API_KEY`、`ASSISTANT_BASE_URL`、`ASSISTANT_MODEL` | 可选帮助助手（在 `frontend/` 里 `npm run assistant`）用的模型。不设置时沿用 `backend/.env` 里的 `OPENAI_*`。它调用的是 OpenAI 兼容的对话接口，所以 `ASSISTANT_BASE_URL` 也可以指向其他厂商的兼容接口。 |
