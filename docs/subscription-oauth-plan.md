# Plan: subscription-backed summaries, and where OAuth PKCE fits

Written **2026-09-03** in response to a proposal to adopt the Pi / Oh My Pi pattern —
loopback OAuth 2.0 PKCE in the app, tokens in the OS keychain, a 401→refresh interceptor —
for Claude Pro, ChatGPT Plus, GitHub Copilot and OpenRouter. Verified against vendor docs
today; `inference-subscription.md` (2026-08-29) still stands and this builds on it.

> **Sequencing:** `claude-subscription-plan.md` comes first — the existing Claude path is
> proved live and hardened there, and every CLI-spawn provider below inherits that work.

---

## 1. Verdict per vendor

The proposal's own last paragraph is the decisive one: *"register as an authorized
third-party OAuth application … so you don't violate terms of service by using undocumented
consumer web endpoints."* Pi and Oh My Pi do not do that — they reuse the vendors' own CLI
client IDs. Whether we may do the same is a per-vendor question:

| Vendor | What the proposal would do | Vendor's position (today) | This app's route |
|---|---|---|---|
| **Anthropic** (Pro/Max) | PKCE against claude.ai with Claude Code's client id, bearer to the Messages API | Compliance page: *"Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials … developers may not collect, store, or intermediate Claude.ai credentials or session tokens."* Explicitly allowed: *"an end user signing in to the unmodified Claude Code binary with their own Claude subscription."* | **Keep `claude-subscription`** (built, D30). Do not build PKCE. |
| **OpenAI** (ChatGPT Plus/Pro) | PKCE against auth.openai.com with Codex's client id, calls to the ChatGPT backend | Not documented for third parties; "Sign in with ChatGPT" for third-party apps is an identity provider only. `codex exec` is documented, headless, reads stdin, enforces a JSON schema. | **New `codex-subscription`**: spawn `codex exec`, same mechanism as Claude. |
| **GitHub Copilot** | GitHub device flow with VS Code's client id → `api.githubcopilot.com` | Not documented for third parties. But GitHub ships the **Copilot SDK** (Python included): runs the Copilot CLI in JSON-RPC server mode on the *user's own* subscription, CLI bundled with the Python package. | **Optional `copilot-subscription`** via the SDK. Park until terms are read. |
| **OpenRouter** | PKCE → API key | **Documented for third-party apps.** `https://openrouter.ai/auth?callback_url=…&code_challenge=…&code_challenge_method=S256`; localhost callbacks on any port, no registration; exchange at `POST /api/v1/auth/keys` with `{code, code_verifier, code_challenge_method}`; returns a **perpetual API key**, not a token — no refresh. Code single-use, 10-minute expiry. | **New `openrouter` provider** + the PKCE connect flow. The one place the proposal applies verbatim. |

Two honest caveats. OpenRouter is prepaid credits, not a subscription — it gives one login
to many models, not free inference. And the only two *subscriptions* this machine's owner
can actually spend are Claude (done) and ChatGPT (phase C below).

The proposal's other three points are already answered here:

- **Secure storage** — `SecretStore` → `keyring` → Windows Credential Manager, write-only
  `PUT /api/settings/secrets`. Reuse; add a secret name.
- **Refresh interceptor** — not needed. OpenRouter keys do not expire; the CLIs refresh their
  own sessions internally.
- **Manual-paste fallback** — required, not optional: this repo is developed in WSL2 with
  interop off, exactly the case the proposal names.

---

## 2. Phases

### A. PKCE loopback helper — `app/llm/oauth_pkce.py` (~150 lines)

Built once; the Google Calendar plan in `SECURITY-AND-AUTH.md` §7 step 3 needs the same
thing later.

- `pkce_pair()` → `(verifier, challenge)`; 32 random bytes, base64url, SHA-256 (RFC 7636).
- `LoopbackFlow`: `http.server` on `127.0.0.1:0` in a daemon thread, accepts one request,
  checks `state`, captures `code`, answers a tiny "you can close this" page, then stops.
  Configurable timeout (default 120 s). Wrong `state` → 400 and keep listening.
- `paste(text)`: accepts a full callback URL *or* a bare code, for the WSL/remote case.
- Browser launch via `webbrowser.open` (already how the tray opens the UI).
- **Not** a FastAPI route: the app cookie is `SameSite=Strict`, so a redirect arriving from
  `openrouter.ai` would land without it and hit the "open from the tray" page. A separate
  listener avoids cutting a hole in the auth middleware and matches the calendar design.
- One pending flow at a time, owned by `Services`; verifier lives in memory only and is
  dropped after exchange.

### B. OpenRouter provider

- `app/llm/openrouter_client.py`: `OpenRouterClient(OpenAiClient)` — base URL fixed to
  `https://openrouter.ai/api/v1`, secret `openrouter`, `llm.openrouter_model`
  (default `anthropic/claude-sonnet-5`), `name = "openrouter"`. Everything else — JSON mode,
  repair loop, 401→`PermanentError(auth)`, 429→`RecoverableError` — is inherited.
- `config.py`: `openrouter` in the `llm.provider` choices and `SECRET_NAMES`;
  `OPENROUTER_API_KEY` env alias.
- Routes:
  - `POST /api/llm/oauth/start` `{provider:"openrouter"}` → `{auth_url, expires_at}`; starts
    the listener, opens the browser.
  - `GET  /api/llm/oauth/status` → `pending | done | failed | expired` (+ error text).
  - `POST /api/llm/oauth/paste` `{value}` → same as a callback.
  - On code: `POST https://openrouter.ai/api/v1/auth/keys` via injectable transport, store
    the key, publish `settings changed=[secret:openrouter]`, forget the verifier.
- `GET /api/llm/status`: a row `needs: "oauth"`, `ready = key present`,
  `console: https://openrouter.ai/settings/keys`.
- UI (`ProviderSettings.tsx`): **Connect** button; poll status every 2 s while pending;
  after the timeout show a paste field; **Disconnect** deletes the secret. Revocation on
  OpenRouter's side is the user's, in their dashboard — say so in the row.
- Also paste-a-key still works, as with every keyed provider.

### C. ChatGPT subscription via the Codex CLI

- Extract the vendor-neutral part of `ClaudeCliClient` into `app/llm/cli_client.py`:
  `CliClient` + a `VendorSpec` (executable, version args, `build_args`, envelope parser,
  not-signed-in / rate-limited markers, env keys to strip). Claude and Codex become specs.
  The eight existing Claude tests must pass unchanged.
- Codex invocation (verified flags):
  ```
  codex exec --skip-git-repo-check -s read-only -C <app scratch dir> \
      --output-schema <schema.json> -o <answer.json> [-m <model>] -
  ```
  Prompt (instruction + schema note + window) on **stdin** via `-`. Read the answer from the
  `-o` file, never stdout, so progress output cannot contaminate parsing. `--output-schema`
  makes Codex enforce our schema itself; keep the repair loop regardless. Strip
  `OPENAI_API_KEY` from the child env so the ChatGPT login is what gets used. Schema and
  answer files live in the app's data dir with 0600 and are deleted afterwards.
- Readiness: `codex --version` for installed; `codex login status` (exit 0) for signed-in —
  a `signed_in` field the Claude row does not have. Sign-in launches `codex login` in a new
  console, exactly like `/api/llm/signin` does for Claude today; the route takes a provider.
- Config: `llm.codex_cli_path`, `llm.codex_model`, `llm.codex_cli_timeout_s`.
- Not the default, not advertised, same footing as Claude: OpenAI's consumer terms have not
  been read. Record it in `DECISIONS.md` D32.
- **First action on a machine with Codex installed:** `codex exec --help`, then Settings →
  Test. `codex` is not on this WSL box.

### D. (Optional) Copilot via the Copilot SDK

`pip install` the Python SDK, which bundles the CLI; the SDK talks JSON-RPC to it and uses
the user's GitHub login. Cheap to prototype behind the same `CliClient` idea, but read the
Copilot terms first; treat like C.

### E. Tests, docs, gates

- **Unit:** RFC 7636 appendix-B vector (`dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` →
  `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`); listener accepts the callback, rejects a
  wrong `state`, times out, and `paste` parses both forms; exchange request shape through
  the injected transport; key stored and verifier gone; OpenRouter request shape; Codex
  client tests mirroring the Claude eight; `test_cli_clients_never_see_a_credential` scanning
  both spec modules.
- **Integration:** `/api/llm/oauth/*` against a fake provider; `llm_status` rows.
- **e2e:** Connect flow in Playwright with `scripts/e2e_server.py` completing the flow
  instantly.
- Regenerate `tests/goldens/openapi.json`; run the usual gate
  (`ruff`, `mypy app`, `pytest`, `selftest all`).
- Docs: D32; `inference-subscription.md` §7 (Codex row → built, OpenRouter row added);
  `current-state.md` §6; config-key table.

---

## 3. Order and size

| Phase | Size | Do it if |
|---|---|---|
| C — Codex | ~1 day | you hold ChatGPT Plus/Pro: the only unbuilt *subscription* |
| A + B — OpenRouter | ~1 day | you want one login for many models, or the PKCE helper for Calendar |
| E | ~½ day | always |
| D — Copilot | ~½ day | you hold Copilot and its terms allow it |

Recommended: **C, then A+B, then E.** Anthropic stays exactly as built.

## Sources

- [Claude Code — legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) (Authentication and credential use)
- [OpenRouter — OAuth PKCE](https://openrouter.ai/docs/use-cases/oauth-pkce)
- [Codex CLI — developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli) (`codex exec` flags, `codex login status`)
- [GitHub Copilot SDK](https://github.com/github/copilot-sdk)
- [Sign in with ChatGPT](https://help.openai.com/en/articles/20001410-sign-in-with-chatgpt)
