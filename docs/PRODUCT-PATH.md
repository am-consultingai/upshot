# Keeping the product door open

"Eventually a real product" doesn't mean building a product now. It means making the
handful of decisions that are **free today and expensive to reverse later**, and
explicitly refusing the rest.

The test for everything below: *does deferring this cost more than an hour later?*

---

## 1. Decisions to lock in now (cheap)

### A. Scope ceiling: sensitive tier, forever

**Rule: the app never requests a restricted scope.** No Gmail API, no full Drive, ever.

For a one-user tool this saves paperwork. For a product it's the difference between a
free 2–6 week verification and an **annual, paid, third-party CASA assessment** — thousands
of dollars a year, redone every 12 months, on a treadmill you can't get off once customers
depend on the feature. Every "wouldn't it be nice if it also read my inbox" idea dies at
this line.

Cost today: zero. It's already the design.

### B. Two credential modes, config-driven from day one

Today you use **your own** Cloud project ("BYO client"). A product ships **one verified
client** that everyone consents to. These differ only in where `client_id`/`client_secret`
come from — so read them from provider config, never hardcode, and keep BYO as a permanent
supported mode (privacy-conscious and enterprise users will want it, and it's your escape
hatch if verification ever stalls).

Cost today: ~1 hour. Cost later: touching every auth path.

### C. Delivery stays SMTP-abstract

`Delivery` takes host/port/user/password. That means: your App Password today, a
transactional relay (SES, Postmark) for a product tomorrow, a customer's corporate SMTP
for an enterprise deal — with zero changes to the pipeline. And it keeps rule A intact,
because the moment delivery goes through the Gmail API you're in restricted-scope territory.

Cost today: zero. It's the interface you'd write anyway.

### D. One capture interface, one implementation

`AudioCapture` with a WASAPI implementation behind it. Don't build a macOS backend — just
don't let WASAPI calls leak into the recorder, the queue, or the pipeline.

This matters more than it looks: most meeting-notetaker users are on macOS (Meetily and
anarlog are both mac-first), and macOS system-audio capture is genuinely hard — it needs
ScreenCaptureKit or a virtual audio driver, not a flag. If that day comes you want to
write one class, not untangle a codebase.

Cost today: one interface. Cost later: a rewrite of the recorder.

### E. Local-first is the product, not an implementation detail

Audio never leaves the machine. That's simultaneously the marketing claim ("your meetings
never touch our servers"), the compliance story (GDPR, recording law, enterprise
procurement), and the reason someone picks this over Granola.

So: **the core loop must never require a server.** The `remote-worker` profile is a
*user-owned* worker — their desktop, their tailnet — never a hosted service. The day
there's a mandatory cloud hop, the differentiator is gone and you're competing with
funded companies on their turf.

### F. Decide the license before the code is public

Meetily is MIT with a paid PRO tier, and its PRO tier is *exactly* the automation layer
you're building — calendar, auto-detect, export. That open-core pattern is available to
you, but pick it deliberately: MIT means Meetily can absorb whatever you build, and
absorbing it is clearly on their roadmap.

Cost today: a decision. Cost later: unwindable.

### G. A consent policy hook

One-party-consent jurisdictions (Israel, most of the US) let you record a conversation
you're part of. All-party states and much of the EU don't. A tool used only by you is
your problem; a tool with users needs the hook to exist: visible recording indicator
(already in the design), an optional announcement, retention defaults, and per-meeting
opt-out.

Cost today: a settings key and a red tray icon you're building anyway.

---

## 2. Deliberately NOT building now

- User accounts, billing, licensing, a hosted anything
- A macOS backend
- Telemetry (if ever: opt-in, anonymous `install_id`, nothing else)
- Multi-tenancy, teams, sharing, a web-hosted UI
- Google verification submission — that's a milestone for when there's a **second user**, not before

---

## 3. What "product" would actually require, when the time comes

**Verification checklist** (free, 2–6 weeks, all achievable because of rule A): a homepage
on a domain you own, a privacy policy URL, verified domain ownership, app name and logo,
a demo video showing the OAuth flow and each scope's use, and a written justification per
scope. Nothing here is a blocker — it's a week of work and a wait.

**Unit economics:** ~$0.05/meeting on Sonnet 5, ~$0.13 on Opus 5, transcription free
(user's own hardware). At 80 meetings/month that's $4–10 of COGS per active user against
competitors charging $14–25. BYO-key sidesteps it entirely for v1.

**The wedge:** Hebrew. Granola doesn't support it at all; Meetily can't load the ivrit.ai
fine-tunes (the PRs are open and unmerged); no paid tool uses them. "The meeting notetaker
that actually works in Hebrew" is a defensible position in a crowded market, and it means
the ivrit models and the glossary loop stay first-class features — not a locale setting.

---

## 4. What this changes in `DESIGN.md`

Almost nothing — which is the point. Concretely: credentials come from config rather than
constants (B), delivery is an SMTP interface (C), capture sits behind an interface (D), and
`remote-worker` is documented as user-owned only (E). Everything else stands.
