# Can the app run inference on a subscription instead of an API key?

Findings as of **2026-08-29**, verified against each vendor's own documentation rather
than recalled. The short version:

> **No vendor sells subscription-backed API access to third-party apps.** What all three
> *do* have is a first-party CLI that signs in with an account. An application can spawn
> that CLI — and if it does, it must never touch the credential the CLI created.

---

## 1. Subscriptions and API access are separate products

| Vendor | Consumer subscription | API access |
|---|---|---|
| **Anthropic** | Claude Pro / Max / Team / Enterprise | Billed separately. Their help centre carries the FAQ *"I have a paid Claude subscription… Why do I have to pay separately to use the Claude API and Console?"* |
| **OpenAI** | ChatGPT Plus / Business | Billed separately; ChatGPT and the API platform have independent billing |
| **Google** | Google AI Pro / Gemini app | Separate — **but the Gemini API has a real free tier**, authenticated with an AI Studio key |

## 2. "SSO" exists, and it carries identity, not billing

All three offer an account login. None of it lets a third-party app spend a subscription:

| Vendor | Login | Who it is for | Third-party inference on your plan? |
|---|---|---|---|
| Anthropic | browser OAuth via `claude` / `ant auth login` | Claude Code and *"other native Anthropic applications"* | **No** — see §3 |
| OpenAI | "Sign in with ChatGPT" | Codex CLI / IDE / desktop for *"subscription access"*; also an **identity provider** for third-party apps (Airtable, Notion, Vercel…) | **No** — it tells an app who you are |
| Google | Google account in the Gemini CLI | Gemini CLI / Code Assist | **No** — the API takes an AI Studio key |

**No vendor verification is required in any path.** That burden belongs to delegated-OAuth
models, where an app acts on a user's behalf with scopes and the vendor reviews it — the
Google Calendar situation `SECURITY-AND-AUTH.md` §1 was written to avoid. An API key needs
no client registration, no consent screen, no review.

## 3. Anthropic's rule, quoted

From the Agent SDK overview:

> *"Unless previously approved, Anthropic does not allow third party developers to offer
> claude.ai login or rate limits for their products, including agents built on the Claude
> Agent SDK. Use the API key authentication methods described in the Quickstart instead."*

From the Claude Code compliance page: third-party developers may not offer claude.ai login
inside their own applications, route other users' requests through Free/Pro/Max
credentials, or *"collect, store, or intermediate Claude.ai credentials or session
tokens."*

Meanwhile the help centre counts *"Claude Agent SDK usage in your own projects"* against a
plan's monthly credit, and notes that *"teams running shared production automation should
use Claude Platform with an API key."*

**How those reconcile:** the subscriber, on their own machine, with their own login, using
their own plan — fine. A product that logs *other people* into claude.ai, or that handles
their credentials — not, without prior approval. This is why the feature exists in the
build but is **not the default and is not advertised**.

## 4. The mechanism that makes it legitimate

The application never authenticates, because it never makes the request:

```
summarize stage ──spawn──► claude -p --output-format json ──HTTPS──► Anthropic
                ◄──JSON on stdout──
```

The credential stays inside Claude Code, where Anthropic put it. Three things enforce this
rather than merely intending it (`app/llm/claude_cli.py`, `DECISIONS.md` D30):

1. `test_claude_cli_never_sees_a_credential` scans the module for `keyring`, `api_key`,
   `Authorization`, `session_token` and `.credentials`; all must be absent.
2. `ANTHROPIC_API_KEY` is stripped from the child's environment — with it set, Claude Code
   offers to use the key instead of the subscription session, which is the opposite of the
   point.
3. Tools are removed from the subprocess's context
   (`--disallowed-tools Bash,Read,Write,Edit,…`) and `--dangerously-skip-permissions` is
   never passed. Summarizing a transcript has no business reading the user's disk.

Prompt delivery: the instruction and schema go as the argument, the transcript window is
piped to **stdin**, so a large window never approaches a command-line length limit.

## 5. What that path costs

Only the Anthropic Messages API enforces our schema server-side. Everything else shares
one validate-and-repair loop (`app/llm/repair.py`).

| Lost | Mitigation |
|---|---|
| `output_config.format` schema enforcement | repair loop: up to 2 retries with the validation error fed back |
| `count_tokens` for windowing | estimated at 2.0 chars/token — deliberately low, so windows come out smaller. `DESIGN.md` §9.1 warns a character heuristic silently blows the window on Hebrew |
| `usage.cache_read_input_tokens` | no prefix-cache accounting; matters less when spending plan credit than per-token |
| predictable limits | shared with interactive Claude Code use. A limit mid-pipeline is a `RecoverableError` → existing backoff → **recording is never affected** |

Plus one install per machine: Claude Code is required — `claude-agent-sdk` states
*"Prerequisites: Install Claude Code separately"* and raises `CLINotFoundError` without it.
This build shells out to the CLI directly rather than adding that dependency, which the
Agent SDK docs bless: *"run the CLI as a subprocess with the `-p` flag and
`--output-format json`."*

## 6. On a new machine

```powershell
irm https://claude.ai/install.ps1 | iex     # native Windows 10 1809+; WSL not required
claude                                      # /login — Anthropic's own browser flow
```

Then in Settings pick **Claude Code (your own subscription)** and press **Test**. The row
shows *Installed* / *Not installed* and the CLI version; Sign in is disabled when it is
missing. Claude Code requires a Pro, Max, Team, Enterprise or Console account — the free
Claude.ai plan does not include it.

## 7. The other two vendors

All three CLIs could be driven the same way, but the value differs sharply:

| | CLI | Subscription sign-in | Headless | JSON out | Worth building? |
|---|---|---|---|---|---|
| Anthropic | `claude` | Pro / Max | `-p` | ✅ verified | **built** |
| OpenAI | `codex` | *"Sign in with ChatGPT for subscription access"* | `codex exec` | **unverified** | **Yes, if you have Plus/Pro** — there is no OpenAI API free tier, so this is the only way to use the subscription |
| Google | `gemini` | personal Google account, 60 req/min · 1,000/day | reuses cached credentials | ✅ `--output-format json` | **No** — the Gemini *API* already has a free tier with a key, no CLI install, real `countTokens`, structured JSON |

**Unresolved for OpenAI and Google:** neither states an equivalent restriction in the CLI
docs I read, and **absence of a prohibition is not permission**. Their consumer terms
govern and have not been read. The same reasoning that makes the Claude path defensible
applies — your machine, your account, the vendor's own login, no credential intermediation
— but treat it as personal use, not a shippable feature, until those terms are checked.

Also unverified: whether `codex exec` can emit a machine-readable envelope. The whole
design depends on parsing one, so `codex exec --help` is the first thing to check.

## 8. Recommendation

- **Default: an API key.** It is the only path with server-side schema enforcement, cache
  accounting and predictable limits, and it is what the pipeline is tested against.
- **Cheapest with no payment method: Gemini's free tier** — a key from AI Studio, 30
  seconds, no card.
- **Zero cost and fully private: Ollama**, already built; a `sensitive` meeting forces it
  regardless of the configured provider.
- **`claude-subscription` when you would rather spend plan credit than API credit**, on a
  machine you own, accepting the trade in §5.

Cost, for scale: `DESIGN.md` puts a meeting at **~$0.13 on Opus 5, ~$0.05 on Sonnet 5**.
Twenty meetings a month is about $2.60, or $1 on Sonnet — the subscription question may
not be worth solving.

---

## Sources

- [How can I access the Claude API?](https://support.claude.com/en/articles/8114494-how-can-i-access-the-claude-api)
- [Claude Code — legal and compliance](https://docs.anthropic.com/en/docs/claude-code/legal-and-compliance)
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Claude Code setup — system requirements](https://code.claude.com/docs/en/setup)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [OpenAI — billing in ChatGPT vs the API platform](https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform)
- [Sign in with ChatGPT](https://help.openai.com/en/articles/20001410-sign-in-with-chatgpt)
- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) · [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)
