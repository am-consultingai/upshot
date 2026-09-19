# Google access, verification, and local security

> **Scope note.** Sections 1 and 9–11 apply to the current build: **no Gmail API** (delivery
> is plain SMTP with an app password), local web-UI hardening, and secret storage.
> **Sections 2–8 are post-V1 reference material** for Google Calendar, which is *not* in
> scope for this version and which no phase of `EXECUTION-PLAN.md` implements or gates on.
> They are recorded now because the analysis was done and is expensive to redo — not
> because anything is waiting on them.

The email half of the product needs no Google API at all. The calendar half, whenever it is
built, lives or dies on OAuth policy — that analysis is below.

---

## 1. The rule that decides everything: never touch a restricted scope

Google sorts scopes into three tiers, and the cost step between them is brutal:

| Tier | Example | What it costs you |
|---|---|---|
| **Non-sensitive** | `openid`, `userinfo.email` | Nothing. No review. |
| **Sensitive** | **Calendar read/write** | Verification required *unless an exception applies*. Free, 2–6 weeks if you do it. |
| **Restricted** | **Gmail API** (any of it), full Drive | Verification **plus an annual third-party CASA security assessment** — a few thousand to tens of thousands of dollars, re-done every 12 months, 6–12 weeks for a first pass |

**Conclusion: the app must never request a Gmail scope.** That one decision removes the
entire CASA burden permanently — not deferred, removed.

This changes the delivery design from the original plan:

> ~~Gmail API with `gmail.compose`~~ → **SMTP on `smtp.gmail.com:587` with a Google App Password.**

App Passwords still work on **personal Gmail accounts** with 2-Step Verification on
(they're a 16-character key for clients that can't speak OAuth). They are **not
available on Google Workspace accounts** — admins can't re-enable them. So the email
path depends on which account type you use; see §6.

Bonus property of this split: **email delivery has no dependency on the OAuth token.**
A calendar token failure can't break the thing that tells you a token failed.

---

## 2. Publishing status: Production, not Testing — this is the trap

The intuitive choice for a personal app is to leave the OAuth consent screen in
**Testing** with yourself as the only test user. That silently breaks a background daemon:

> A Cloud project with an external-user-type consent screen in **Testing** status issues
> **refresh tokens that expire 7 days from consent.**

Your app would die every week and demand a browser re-consent — for a tool whose entire
value is that you forget it exists, that's fatal.

**So: publish the app to "In production", unverified.** Then:

| | Testing | Production (unverified) |
|---|---|---|
| Refresh token lifetime | **7 days** ❌ | long-lived ✅ |
| Verification needed | no | **no** — "Personal Use" is a documented exception |
| Warning screen | yes | yes, **once** |
| User cap | 100 test users | 100 total, lifetime, non-resettable — irrelevant at 1 user |

Google's own exception list is explicit: *"One use case is if you are the only user of
your app or if your app is used by only a few users, all of whom are known personally to
you."* You qualify. Publishing does **not** mean submitting for review.

---

## 3. About the warning you can't fully avoid

On a personal Gmail account requesting a sensitive scope, you will see
**"Google hasn't verified this app"** → *Advanced* → *Go to Upshot (unsafe)* — **exactly
once**, at first consent, and only you ever see it. It is not a recurring nag and it does
not appear on subsequent token refreshes.

The three ways to get *no* warning at all:

1. **Google Workspace account + "Internal" user type.** If you have a work domain, this is
   strictly the best option: no verification, no warning, no 7-day rule, no user cap. It also
   removes the App Password option, so email would need a different path (§6).
2. **Complete sensitive-scope verification.** Free, 2–6 weeks of back-and-forth, no CASA
   (because we avoid restricted scopes). Real, but pointless for a one-user tool.
3. **Don't use OAuth at all** — see §5.

---

## 4. The scopes to actually request

Minimum viable set, in order of preference:

| Scope | Tier | Why |
|---|---|---|
| `calendar.events.readonly` | sensitive | Read events to build the arm plan and pull titles/attendees/agenda. Narrower than `calendar.readonly`, which also exposes calendar lists and settings |
| `userinfo.email` | non-sensitive | Identify which account is connected; costs nothing |
| `calendar.app.created` *(optional)* | narrower | Lets the app create and write **only its own** secondary calendar — for the clickable-link feature in §7. Confirm its exact classification in the Cloud console when you add it; if it's inconvenient, `calendar.events` (sensitive, still no CASA) is the fallback |

**Never:** any `gmail.*`, any full-Drive scope, `calendar` (full), or `contacts`.

---

## 5. The zero-OAuth escape hatch, and why it's plan B

Google Calendar exposes a **"Secret address in iCal format"** — a private HTTPS URL
serving your calendar as `.ics`. Fetching it needs no OAuth, no Cloud project, no consent
screen, and produces no warnings ever.

Why it isn't plan A:

- **Freshness isn't guaranteed.** There's no change notification, only polling, and Google's export is cached to an extent it doesn't document. A meeting moved 20 minutes ago may not be in the feed you fetch. For a *predictive* arming system, stale is the one thing that ruins it.
- **The URL is an unscoped bearer secret.** Anyone who gets it reads your whole calendar forever; the only revocation is regenerating it, which breaks every other consumer.
- **Attendee data is inconsistent.** Attendee names are the input to Whisper's `initial_prompt`, which is where the Hebrew accuracy gain comes from.

Keep it as a documented fallback for anyone who refuses to create a Cloud project — the
`CalendarSource` interface should have both implementations behind it from day one, because
that's a one-hour abstraction now and a rewrite later.

---

## 6. The chosen setup: Workspace calendar + personal Gmail delivery

The two halves are already decoupled, which makes this combination natural:

| | Reads | Sends |
|---|---|---|
| **Work Workspace account** | `calendar.events.readonly` | — |
| **Personal Gmail** | — | SMTP + App Password |

Workspace accounts can't create App Passwords, and personal Gmail can't give you a
warning-free Internal app — so using each account for the half it's good at is strictly
better than forcing either to do both.

### But the work calendar has a hard gate — test this before designing around it

Google Workspace admins control third-party API access under **Security → API Controls →
App Access Controls**, and **unconfigured third-party apps are blocked by default**. This
is not the "unverified app" warning you can click past. It's a refusal:

> **"Access blocked: … has not completed the Google verification process"** /
> *"Your admin has not allowed this app"* — with **no Advanced → proceed escape hatch.**

Distinguishing the two failure modes is the whole test:

| What you see at consent | Meaning | Fix |
|---|---|---|
| "Google hasn't verified this app" + **Advanced → Go to (unsafe)** | Normal unverified-app screen | Click through. Done forever. |
| "Access blocked" / "admin has not allowed this app", **no way forward** | Workspace policy | Needs an admin to allowlist your client ID, or fall back |

**Five-minute test, before any code:** create the Desktop OAuth client, request
`calendar.events.readonly`, and run one consent against the work account. Which of the two
screens appears decides the entire calendar strategy.

### The three paths, in order of preference

1. **Cloud project owned by the Workspace org, user type "Internal".** No verification, no warning, no 7-day rule, no user cap. Requires the project to belong to the organization's Cloud Organization resource — i.e. rights to create projects in the org, which usually means being an admin or knowing one.
2. **Cloud project on the personal account, External + Production + unverified**, work account added as a user. One warning click — *if* the Workspace admin allows unconfigured apps. Ask them to allowlist the client ID; a read-only calendar scope for one employee's own calendar is an easy ask.
3. **Blocked and no admin help:** fall back to the work calendar's **secret iCal URL** (§5) — no OAuth, so App Access Controls never enter the picture — accepting the freshness caveats. Or arm from a personal calendar that mirrors work events.

### One non-technical note

Recording work meetings may be governed by your employer's policy regardless of what
Google's API permits. Worth a five-minute check; it isn't a technical control.

## 7. The clickable-link-from-the-calendar feature

Three ways to get from a calendar entry to the summary, in increasing scope cost:

1. **No calendar write at all (M2 default).** The local UI has its own agenda view, and the delivery email carries the link. Zero extra scope, zero risk.
2. **A dedicated "Meeting Summaries" calendar (recommended).** The app creates its *own* secondary calendar and drops a short event at the meeting's time: *"📄 Summary: Weekly Sync"*, link in the description. Overlays cleanly on your normal view, works for meetings **organized by other people** (which you often can't edit), and uninstalling is one click — delete the calendar and every trace is gone. This is why it beats option 3.
3. **Write into the original event's description.** Nicest when it works, but you can only edit events you organize, so it silently fails for exactly the meetings someone else booked. Needs the broader `calendar.events` write scope for a partial feature.

**Link shape:** `http://127.0.0.1:8000/m/<meeting_id>`. Clicking it from Google Calendar in a
desktop browser on the same machine works. **It is dead on your phone** — accept that, or
later expose the UI over a tailnet. Don't build a public relay for this.

---

## 8. Running in the background (a Windows constraint that decides the shape)

**It must be a user-session tray app, not a Windows Service.** Services run in Session 0,
which has **no audio endpoints** — a service literally cannot open WASAPI capture. Anyone
who "hardens" this into a service later will find recording silently broken.

- **Autostart:** Task Scheduler *at logon* (with a 30–60s delay and restart-on-failure) beats a Startup-folder shortcut — it survives crashes and logs its own failures.
- **Single instance:** named mutex; a second launch focuses the existing tray icon.
- **Supervision:** the tray process supervises the recorder and worker; a worker crash never takes the recorder down, because rule 1 is that recordings aren't lost.
- **Sleep/hibernate:** subscribe to power-broadcast events. Sleeping mid-meeting must finalize the current chunk cleanly and mark the meeting `INTERRUPTED`, not corrupt it.
- **Visible state:** the tray icon is the consent surface — amber = armed, red = recording. Never record with no visible indicator.

---

## 9. Securing the local web UI

The most-overlooked attack surface here: a FastAPI on localhost holding every transcript
of every meeting you've ever had.

- **Bind `127.0.0.1` explicitly**, never `0.0.0.0`. On a laptop that's the difference between "local tool" and "transcript server for the coffee shop wifi".
- **DNS rebinding defense.** A malicious website can resolve its own hostname to 127.0.0.1 and make *your* browser talk to the app. Validate the `Host` header against an allowlist (`localhost`, `127.0.0.1`, plus the port) and reject everything else. This is the one that gets missed.
- **CORS: deny all.** No cross-origin reads, `SameSite=Strict` cookies, CSRF token on every mutating route.
- **Auth:** a per-install secret. First launch opens the browser with a one-time token that sets a long-lived `SameSite=Strict` cookie. Calendar links then just work; a request with no cookie gets a page saying how to authorize instead of your transcripts. The token is what keeps *other software on the same machine* out — the Host check and CSRF only stop websites. Each browser, or browser profile, needs its own token, once, and there are two ways to get a fresh one, both reachable only by the person at the computer: **Open dashboard** in the tray mints a new link on every click, and **N** in the launcher window asks the app for one with a key the app writes to its own home folder (`launcher.key`, rewritten each start, answered at `POST /api/auth/link` before CSRF and routing).
- **Secrets at rest:** OAuth refresh token, SMTP app password, and the Anthropic key go in **Windows Credential Manager** (via `keyring`) — not a `.env` file next to the code. An app password is full send-as-you mail access; treat it like one.
- **Data at rest:** transcripts and audio are plaintext on disk. Document that BitLocker is the answer, and make the retention policy (auto-delete raw audio after N days) real rather than aspirational.
- **Egress is auditable:** exactly two outbound destinations in normal operation — Google (calendar) and Anthropic (summaries), plus SMTP. Log every outbound call with meeting id and byte count so "what left this machine" is answerable. The `sensitive` flag must be enforced at the egress boundary, not just in the UI.

---

## 10. First-run sequence (what the user actually does, once)

1. Install, launch. Tray icon appears; browser opens the local UI.
2. **Google:** "Connect calendar" → a one-page wizard walks through creating a Cloud project, enabling the Calendar API, creating a **Desktop app** OAuth client, and **publishing to Production**. Paste the client JSON. (This is the ugly step. It's also unavoidable for anyone who wants no warning and no CASA — and it's a one-time, 10-minute cost.)
3. Consent in the browser via the **loopback redirect** (`http://127.0.0.1:<random port>/`) with **PKCE** — the sanctioned desktop flow since OOB was deprecated. Click through the unverified screen once.
4. **Email:** paste a Google App Password (with a link to the page that generates it), or point at any other SMTP server.
5. **Anthropic key.**
6. Pick audio devices; a 5-second test recording proves both tracks capture.
7. Done. The app arms itself for tomorrow.

Re-auth is graceful: if the refresh token dies (revoked, password change, 6 months idle,
>100 tokens issued for the same client+account), the UI shows a banner, the tray icon goes
grey, **and the recorder keeps working** — meetings still get captured and queued; only the
calendar arming degrades to manual until you reconnect.

---

## 11. Sources

- [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification) · [Restricted scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) · [Manage app audience (Testing vs Production)](https://support.google.com/cloud/answer/15549945?hl=en) · [Using OAuth 2.0 to access Google APIs](https://developers.google.com/identity/protocols/oauth2) · [Choose Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth)
- [Google CASA assessment overview](https://deepstrike.io/blog/google-casa-security-assessment-2025) · [Security assessment (Cloud console help)](https://support.google.com/cloud/answer/13465431?hl=en)
- [7-day refresh token expiry explained](https://www.unipile.com/google-oauth-refresh-token/) · [Gmail App Passwords: setup and gotchas](https://cli.nylas.com/guides/gmail-app-password-setup) · [Transition from less secure apps to OAuth](https://support.google.com/a/answer/14114704?hl=en)
