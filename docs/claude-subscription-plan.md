# Plan: prove the Claude subscription path first

**2026-09-03.** `claude-subscription` (`app/llm/claude_cli.py`, D30) is built and unit-tested
against an injected runner, but has never made a real call (`current-state.md` §4). This is
the first provider to prove live, and the test bed for everything in
`subscription-oauth-plan.md`, which now comes after this.

Probed today on this WSL box with Claude Code **2.1.259**, signed in as a Max plan
(`claude auth status`). Each probe was a single tiny Sonnet call.

---

## 1. What the probes found

| # | Finding | Consequence |
|---|---|---|
| 1 | **The provider is broken as written.** `--disallowed-tools <tools...>` is variadic; the instruction placed after it is consumed as tool names. The CLI answered *"Input must be provided either through stdin or as a prompt argument"*. In the app the transcript is on stdin, so the CLI would have run the transcript with **no instruction and no schema**. | Fix the argument shape (§2.1). A unit test on arg order would not have caught this; only the live call did. |
| 2 | `--system-prompt <text>` + transcript on stdin + `--json-schema <schema>` works. The envelope carries `structured_output` (already parsed), `result` (the JSON string), `usage` with `cache_creation_input_tokens` / `cache_read_input_tokens`, `modelUsage`, `total_cost_usd`, `is_error`, `terminal_reason`, `api_error_status`. | Server-side schema enforcement and cache accounting are **not** lost on this path after all — `inference-subscription.md` §5 is out of date. |
| 3 | Signed out: exit 1, `is_error: true`, `terminal_reason: "api_error"`, `result: "Not logged in · Please run /login"`. | Existing `NOT_SIGNED_IN` markers match. Keep. |
| 4 | `claude auth status` prints JSON — `loggedIn`, `authMethod: "claude.ai"`, `subscriptionType: "max"`, `email` — spends nothing. `claude auth login` / `logout` exist. | Settings can show *signed in as … (max)*, and Sign in can run `claude auth login` instead of the interactive REPL. |
| 5 | `--tools ""` removes every built-in tool; `--strict-mcp-config` ignores the user's MCP servers; `--setting-sources ""` ignores user/project settings. | Today's deny-list leaves the user's **MCP servers** (mail, LinkedIn, Drive on this machine) loaded into the summarizer. Must close. |
| 6 | Even with a two-line system prompt the call created **~15k cache tokens** and cost $0.06 on Sonnet: Claude Code's own tool and context preamble. | Plan credit, not dollars, but it is what a map window costs. Measure with `--tools ""`; expect the 5-minute cache to absorb it across windows of one meeting. |
| 7 | The child inherits `CLAUDECODE`, `CLAUDE_CODE_*` and friends when the app is launched from a Claude Code shell. | Strip `ANTHROPIC_*` and `CLAUDE*` by prefix, not just `ANTHROPIC_API_KEY`. |

---

## 2. Work

### 2.1 Fix the CLI contract — `app/llm/claude_cli.py`

```
claude -p --output-format json
       --system-prompt <instruction>          # system blocks joined; no positional prompt
       --json-schema <schema JSON>            # CLI enforces it; repair loop stays as backstop
       --tools "" --strict-mcp-config --setting-sources ""
       --model <llm.model> --effort <llm.effort>
       --max-turns 3                          # structured output is a tool call: 2 turns
       [llm.claude_cli_args…]
  < transcript window on stdin
```

- Drop the positional instruction entirely; the variadic problem cannot recur.
- Read `structured_output` first; fall back to `result`, then the repair loop.
- Map `usage` into `LlmResult.usage` so `cache_read_tokens` and the existing accounting
  work; `model` from `modelUsage` (largest entry); keep `total_cost_usd` in usage too.
- `is_error` + `terminal_reason`/`api_error_status`: `401/403` or the not-logged-in text →
  `PermanentError(auth)`; `429`/`overloaded`/usage-limit → `RecoverableError`; else
  `RecoverableError` with the first 300 chars.
- Environment: drop every `ANTHROPIC_*` and `CLAUDE*` key. Run in an **empty scratch
  directory** under the app data dir so no `CLAUDE.md` is picked up.
- `status()` additionally runs `claude auth status` and returns `signed_in`, `auth_method`,
  `subscription`, `email` (no secret; email is display only).
- `login_command()` → `[claude, "auth", "login"]`.
- Remove `llm.claude_cli_disallowed_tools`; add nothing else. `test_claude_cli_never_sees_a_credential` keeps its scan.

### 2.2 Settings and API

- `GET /api/llm/status`: the `claude-subscription` row gains `signed_in`, `detail`
  becomes *"2.1.259 · signed in as … (max)"* or *"installed, not signed in"*.
  `ready = installed and signed_in`.
- `POST /api/llm/signin`: launch `claude auth login` in a new console on Windows; on
  Linux/WSL return the command to run, as now.
- `ProviderSettings.tsx`: show the detail string; Sign in enabled when installed and not
  signed in; **Test** unchanged.

### 2.3 The live test — the point of this phase

1. **Marker** `live_cli` in `pyproject.toml`, deselected by default like `live_api`.
   `tests/live/test_claude_cli_live.py`, skipped unless `claude auth status` says
   `loggedIn`:
   - probe schema → `ok: true`, `attempts == 1`, `usage` non-empty;
   - a 3-minute English fixture transcript → `NOTES_SCHEMA` validates, `attempts == 1`;
   - the same in **Hebrew** — the reason the character heuristic exists;
   - signed-out simulation with `HOME` pointed at an empty dir → `PermanentError(auth)`.
2. **End to end:** `scripts/demo.sh` with fakes for audio and ASR and
   `UP_LLM__PROVIDER='"claude-subscription"'` → the pipeline produces a real summary from
   the fixture transcript; open it in the timeline; check the RTL render and `data-at-ms`.
3. **Measure and record** (`docs/install-log.md`): wall time per window, `total_cost_usd`
   and cache hit ratio across a meeting's windows, and real `input_tokens` versus the
   2.0 chars/token estimate on the Hebrew fixture — then set the estimate from data.
4. **Windows:** the same three steps on the Windows box after `irm https://claude.ai/install.ps1 | iex`
   and `claude auth login`; `shutil.which("claude")` resolves `claude.exe`/`.cmd` there.
   Record in `docs/windows-run.md`.

### 2.4 Unit tests to add or change

- `build_args` has no positional prompt, carries `--system-prompt`, `--json-schema`,
  `--tools ""`, `--strict-mcp-config`, `--model`.
- Envelope: `structured_output` preferred; `result` fallback; `is_error` with
  `api_error_status` 401 → auth, 429 → recoverable.
- `status()` parses `auth status` JSON; malformed output → `signed_in=False` with detail.
- Runner env: no key starting with `ANTHROPIC_` or `CLAUDE` reaches the child.
- Regenerate `tests/goldens/openapi.json`; the usual gate.

### 2.5 Docs

- `inference-subscription.md` §4 (mechanism) and §5 (the "lost" table shrinks to
  `count_tokens` and shared limits); §6 install steps use `claude auth login`.
- `DECISIONS.md` D30 addendum: tools removed with `--tools ""` and MCP disabled, why.
- `current-state.md` §4: first real LLM call made, through which provider, with numbers.

---

## 3. Order

1. §2.1 with its unit tests (half a day).
2. §2.3 steps 1–3 here in WSL — this is the test (half a day, a few dollars of plan credit).
3. §2.2 and §2.5 (half a day).
4. §2.3 step 4 on Windows when that box is available.

Then `subscription-oauth-plan.md`, with the CLI-spawn base already hardened by this work.
