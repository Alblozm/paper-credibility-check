# Model setup

**English** · [中文](models.zh-CN.md)

PaperTrace runs each assessment with the model picked in the start page's **Model** menu. The backend supports four vendors: OpenAI (GPT), Anthropic (Claude), Google (Gemini) and DeepSeek. Use your own API key from each vendor you want. One vendor is enough; with several keys, users choose per run.

All settings go in `backend/.env`. Create it from the example once, then restart the backend after every change:

```bash
cd backend
cp .env.example .env
```

## 1. Switch on real parsing

The example `.env` starts in demo mode (`LLM_PROVIDER=fixture`, `PARSER_PROVIDER=stub`), which returns sample reports. For real assessments set:

```ini
PARSER_PROVIDER=generic   # parse any arXiv paper or uploaded PDF
LLM_PROVIDER=openai       # the model selected by default: openai or deepseek
```

`LLM_PROVIDER` only decides which model is selected when the page opens. If that vendor has no key, the first available model is selected instead, so a Claude-only or Gemini-only setup can keep `LLM_PROVIDER=openai`. Do not leave it at `fixture` with real keys, because reports would then be labelled as demo output.

Start the backend with `make run`. `make run-openai` and `make run-deepseek` set the same two values on the command line, with GPT or DeepSeek selected by default.

## 2. Add a key for each vendor you use

| Vendor | Get a key | Set in `backend/.env` | Default model (override with) |
|---|---|---|---|
| OpenAI (GPT) | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | `OPENAI_API_KEY` | `gpt-5.6-terra` (`OPENAI_MODEL`) |
| Anthropic (Claude) | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) | `ANTHROPIC_API_KEY` and `CLAUDE_ENABLED=true` | `claude-sonnet-5` (`ANTHROPIC_MODEL`) |
| Google (Gemini) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` and `GEMINI_ENABLED=true` | `gemini-3.7-flash` (`GEMINI_MODEL`) |
| DeepSeek | [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys) | `DEEPSEEK_API_KEY` | `deepseek-flash` (`DEEPSEEK_MODEL`) |

Example with all four:

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

After a restart, open `http://127.0.0.1:8000/v1/models`. Every model you configured should show `"is_available": true`.

## Things to know

- **Claude and Gemini need two settings.** A key alone makes the model appear in the menu but greyed out (`is_available: false`); `CLAUDE_ENABLED=true` or `GEMINI_ENABLED=true` makes it selectable. The switch lets you add a key and confirm it with a sample run before users can pick it.
- **The code check's web search runs on OpenAI or DeepSeek.** GPT, Claude and Gemini search the web through OpenAI's search; DeepSeek uses its own. Without `OPENAI_API_KEY`, a Claude or Gemini run skips that web search, and the report records it as disabled. The rest of the code check still runs: links printed in the paper and GitHub lookups. Add an OpenAI key even if you mainly use Claude or Gemini.
- **One model per run.** The five-dimension quality assessment uses the same model as the credibility check of that run.
- **Endpoints.** Claude and Gemini are called through the vendors' official OpenAI-compatible endpoints. `ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `OPENAI_BASE_URL` and `DEEPSEEK_BASE_URL` only need changing if you go through a compatible proxy.
- **Hiding DeepSeek.** `DEEPSEEK_ENABLED=false` removes it from the menu even when a key is set.
- **Keys stay on the server.** Never put a key in the frontend or in any `VITE_` variable, and never commit `.env`.

## Optional keys

| Setting | Effect |
|---|---|
| `GITHUB_TOKEN` | Raises the GitHub rate limit for the code check. A token without extra scopes is enough. |
| `CROSSREF_MAILTO` | Contact email sent to Crossref, as Crossref asks API users to do. |
| `ASSISTANT_API_KEY`, `ASSISTANT_BASE_URL`, `ASSISTANT_MODEL` | Model for the optional help assistant (`npm run assistant` in `frontend/`). Without them it uses the `OPENAI_*` values from `backend/.env`. It talks to an OpenAI-compatible chat endpoint, so `ASSISTANT_BASE_URL` can also point to another vendor's compatible endpoint. |
