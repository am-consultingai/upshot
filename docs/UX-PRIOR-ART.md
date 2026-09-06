# UX prior art — how the category looks, and what ours is missing

Scanned 2026-09-04. Companion to `PRIOR-ART.md`, which asked *should we build this*.
This one asks *why does ours look unfinished next to them*.

**Sourcing caveat, stated once.** I did not install and drive these products. Everything
below comes from vendor documentation, help centres, a live shared Circleback note, and
hands-on reviews — cited inline. Claims from a help centre or a real artefact are
reliable; claims from a landing page are marketing and are marked as such. Where a
detail could not be confirmed it says so rather than guessing.

---

## 1. Per-app profiles

### Circleback — the one to copy first

The clearest information architecture in the category, and verifiable because they
publish real notes ([live example](https://circleback.ai/view/m9vtzdx5fpzle2iypnm)).

A meeting is **three tabs: Notes · Transcript · Action items**. Notes open with an
`Overview` heading, then topic headings (`Circleback product introduction`, `Meeting
capture options` …) — a structured document, not a wall of bullets. A **table of
contents** allows jumping to a portion of the meeting. Action items carry **owners** and
are **checkable in place**. Speakers appear as initials (`AH`). At the foot of the note
is a chat entry point — "Ask about *How Circleback works*" — scoped to that meeting.
[YC's profile](https://www.ycombinator.com/companies/circleback) confirms the model:
notes "grouped by topic, alongside assigned action items, the transcript, and a
recording."

> **What to steal:** the three-tab split; `Overview` + topic headings instead of a flat
> summary; action items as first-class objects with an owner and a checkbox, not prose.

### Fireflies — the most detailed layout documentation

Their [Notepad guide](https://guide.fireflies.ai/articles/6653885315-learn-about-the-fireflies-notepad)
is the most concrete layout spec published by anyone in the category.

**Two panels: summary/notes left, full transcript right**, each expandable to full
screen. A **collapsible icon rail** on the far left holds Smart Search, Index,
Soundbites, Comments, Bookmarks. The summary has a **dropdown to switch preset formats**
(sales template, 1:1 notes, BANT) plus a copy button. The transcript has
**find-and-replace** and an **edit icon with autosave**. Playback shows word-by-word
captions with speed control. Top bar: AskFred (chat), Share, Export, and a three-dot menu
for rename / regenerate notes / update language / download.

Their [2024 redesign notes](https://fireflies.ai/blog/fireflies-notepad-updates/) are
worth reading for what they *removed*: filters were pushed into a pull-out panel to give
the summary and transcript "more space and an uncluttered interface."

> **What to steal:** the two-panel summary↔transcript split; a summary-format dropdown
> (this is your prompt editor, made a first-class control); find-in-transcript;
> regenerate-notes in a menu rather than a naked button.

### Granola — the note-first philosophy

Granola deliberately **does not lead with the transcript**. You type sparse notes during
the call; afterwards it merges them with the transcript into an editable document
([granola.ai](https://www.granola.ai/ai-note-taker)). Reviewers confirm the transcript is
secondary, kept "so you can double check your notes"
([Wee](https://celinewee.medium.com/granola-ai-notepad-177e86eb8d3c)). The output "starts
as an editable note" rather than organising around a recording
([Proser](https://zackproser.com/blog/granola-ai-review)). The left sidebar organises by
**companies, people and team** rather than by date
([max-productive](https://max-productive.ai/ai-tools/granola/)).

Criticism worth noting: start/stop "still need attention" — it does not reliably
auto-capture every calendar event (Proser).

> **What to steal:** the summary as an **editable document**, not read-only HTML. And
> organising by person/company, not only chronology.

### Otter — the transcript-centric incumbent

Otter's [conversation page](https://help.otter.ai/hc/en-us/articles/5093228433687-Conversation-Page-Overview)
carries a Transcript tab with a **"Show only highlights" toggle**, an AI-generated
**Outline**, **Otter Chat**, and **comment threads**. Speakers are labelled numerically
and renamed by clicking the word "Speaker" in the transcript
([PCMag](https://www.pcmag.com/reviews/otter)). Playback controls sit **at the bottom**
with speed control and ±5s skip.

> **What to steal:** rename-a-speaker by clicking them in the transcript; the persistent
> bottom playback bar; an auto-generated outline as a navigation aid.

### Fathom — timestamped, clickable everything

Summaries carry key takeaways, topics and action items that are **clickable and
timestamped** ([BlueDot](https://www.bluedothq.com/blog/fathom-review)), and users can
**mark highlights live during the call** ([Fahim](https://www.fahimai.com/how-to-use-fathom-ai)).
A competitor's review calls it "reliable but plain" ([tl;dv](https://tldv.io/blog/honest-review-of-fathom/))
— useful evidence that plain-but-coherent still reads as professional.

> **What to steal:** every generated claim links back to its moment in the audio. This is
> the single highest-value link in the whole category and we already have `at_ms` on
> every quote and decision — we simply don't render it as a control.

### Meetily — our closest architectural twin

Local-first, mic + system audio, Whisper/Parakeet on device, summaries via Ollama or your
own key ([GitHub](https://github.com/Zackriya-Solutions/meetily)). Screens: a recording
home with **device selection**, a live transcript panel, a summary **editor**, and
settings exposing **storage location and model preferences**. Transcript and generated
record sit **side by side**. Diarisation is beta/PRO; PDF/DOCX/Markdown export is PRO.

> **What to steal:** confirmation that our stack is right, and that side-by-side
> transcript+notes is the local-first norm. Also: they charge for export, which tells you
> people want it.

### Hyprnote / Anarlog — the privacy-first presentation

Same shape as ours: no bot, captures device audio, on-device STT and LLM or bring your
own keys, sessions and transcripts in local SQLite ([anarlog.so](https://anarlog.so/)).
Features include speaker identification **with manual labelling**, **note templates**,
custom dictionaries, folders with access control, in-note chat, exports, and a local
API/CLI/MCP/webhooks surface. Live transcript with speaker ID, Apple Calendar/Contacts
and Obsidian integrations ([FYI Combinator](https://fyicombinator.com/company/hyprnote)).

Their trust signalling is explicit and in the product, not buried in a privacy policy:
*"Your notes, transcripts, attachments, and recordings stay on your device by default"*
and *"nothing appears in the participant list."* Open-source inspectability is presented
as part of the argument.

> **What to steal:** the trust signal as a visible product surface. We are *more* private
> than most of these and say nothing about it anywhere in the UI.

---

## 2. Cross-cutting patterns

**The two-pane meeting screen is the category standard.** Summary/notes on one side,
transcript on the other, both independently expandable (Fireflies explicitly; Meetily
side-by-side; Circleback via tabs on narrow screens). Nobody stacks summary above
transcript in one scrolling column — which is what we do.

**Structured notes beat a blob.** Every serious product renders an overview, topic
headings, and action items as separate typed sections. Circleback and Otter both provide
a table of contents / outline over that structure.

**Action items are objects, not sentences.** Owner, checkbox, completion state
(Circleback). Our schema already carries `who`, `what`, `due` and `confidence` — we throw
that structure away by rendering summaries as opaque HTML.

**Everything links to a timestamp.** Fathom's clickable timestamped highlights, Otter's
playback sync, Fireflies' word-by-word captions. Our `at_ms` exists and is inert.

**Chat-with-the-meeting is now table stakes.** AskFred, Otter Chat, Circleback's "Ask
about…", Hyprnote's in-note chat. Absent here.

**Search is a top-level affordance**, usually a persistent bar (Fireflies' Smart Search
rail; Otter's search). We have SQLite FTS in the schema and no search box.

**Editability.** Fireflies edits transcripts with autosave; Granola's summary is an
editable document; Circleback checks items off. Ours is read-only throughout.

**Organisation beyond chronology.** Granola groups by company/person/team; Fireflies
filters My meetings / Hosted by me / Shared with me; Hyprnote uses folders. A pure
calendar is the weakest possible organisation for finding a meeting later.

**Sharing and export are assumed.** Share links, PDF/DOCX/Markdown, push to Slack/Notion/
CRM. Meetily charges for it, which is a decent proxy for demand.

### Right-to-left: nobody has done this

Repeated searching surfaced **no meeting-notes product with a documented RTL story**.
The generic guidance is the usual: logical properties (`margin-inline-start` over
`margin-left`), mirrored layout, correct bidirectional handling of numbers and Latin
technical terms inside Hebrew text
([Microsoft](https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/user-interface/bidirectional-support),
[txl.co.il](https://www.txl.co.il/post/hebrew-arabic-rtl-localization-design-challenges-and-how-to-solve-them)).

This is not a gap in our product. It is the **one axis where we are structurally ahead**,
and it is worth treating as a headline rather than an implementation detail — particularly
the mixed-direction case this app produces constantly: Hebrew speech containing English
product names and numbers.

---

## 3. Prioritised recommendations, mapped onto our screens

Ordered by how much each closes the "looks unfinished" gap per unit of work.

### Meeting screen — where almost all the deficit is

| # | Change | Why | Evidence |
|---|---|---|---|
| 1 | **Render `notes.json` as structure, not HTML.** Overview, topic headings, decisions, action items as typed sections | We already produce all of it and then flatten it | Circleback, Otter outline |
| 2 | **Action items with owner + checkbox** | The schema has `who`/`what`/`due`; prose throws it away | Circleback |
| 3 | **Click a timestamp → seek the audio.** Every quote and decision carries `at_ms` | The category's defining interaction, and we're one `onClick` from it | Fathom, Otter |
| 4 | **Two panes: summary ⇔ transcript**, each collapsible | Category standard; we stack them in one column | Fireflies, Meetily |
| 5 | **Find-in-transcript** | Expected in every product examined | Fireflies |
| 6 | **Editable summary** | Ours is read-only; every competitor's is not | Granola, Fireflies |
| 7 | **Persistent bottom playback bar** with speed and ±5s | Ours is a bare `<audio>` element | Otter |
| 8 | Speaker rename by clicking a speaker in the transcript | We label `ME`/`THEM`; real names are the point | Otter, Hyprnote |
| 9 | Export — Markdown and PDF | Assumed everywhere; paywalled by Meetily | Meetily, Fireflies |
| 10 | Chat scoped to the meeting | Now table stakes | AskFred, Otter Chat, Circleback |

### Timeline screen

| # | Change | Why |
|---|---|---|
| 1 | **Search across meetings** — one persistent box | FTS already exists in the schema; its absence is conspicuous |
| 2 | **A real empty state** — what the app is, how to start one, that it is local | First-run currently shows an empty grid |
| 3 | **A list view alongside the calendar**, with title, duration, participants, state | A calendar alone is poor for retrieval |
| 4 | Meeting cards showing a one-line TL;DR and action-item count | Turns the list into something scannable |
| 5 | Filters — has summary / needs review / failed | Cheap given the state machine |
| 6 | Grouping by person or tag | Granola's sidebar model |

### Settings screen

| # | Change | Why |
|---|---|---|
| 1 | **Group into sections** with headings — Recording, Transcription, Summaries, Privacy | Currently a flat stack of controls |
| 2 | **Say the privacy story out loud**: audio and transcripts never leave this machine; here is where they live; here is what each provider sends | Our strongest differentiator, currently invisible | 
| 3 | **Promote the prompt editor to named templates** you can pick per meeting | Fireflies' summary-format dropdown |
| 4 | Show storage location and disk used, with a purge control | Meetily surfaces this |
| 5 | Glossary/custom dictionary as a first-class screen | Hyprnote ships it; critical for Hebrew + English product names |

### Cross-cutting

- **Typography and density.** The category converges on generous line height in the
  summary, a smaller monospace-ish transcript, and clear heading hierarchy. Our summary
  is undifferentiated body text.
- **A real recording indicator.** Persistent, unmissable, with elapsed time.
- **Keyboard shortcuts**, at minimum play/pause and seek.
- **Dark mode.** Table stakes for a desktop app in 2026.
- **Own the RTL story** — mixed Hebrew/English is our differentiator, not our tax.

---

## 4. The honest summary

Very little of the gap is missing *capability*. We already record two tracks, transcribe
locally, produce a schema-validated summary with owners, due dates and millisecond
timestamps, and hold it all on the user's own machine — which is more than several
products in this list.

The gap is that we **discard our own structure at the last step**. `notes.json` has
topics, decisions, action items with owners, and timestamps on every quote; the meeting
screen renders it as one undifferentiated block of HTML with a bare audio element beside
it. Items 1–3 under *Meeting screen* are essentially presentation work on data we already
compute, and they are most of the distance between this and something that looks like the
products above.

---

## Sources

All checked 2026-09-04. Three return 403 to a command-line fetch because they block
bots — Medium, Otter's help centre and PCMag — and open normally in a browser. They are
marked †. Everything else resolved 200.

- Circleback — [live shared note](https://circleback.ai/view/m9vtzdx5fpzle2iypnm) · [YC profile](https://www.ycombinator.com/companies/circleback) · [notes comparison](https://circleback.ai/blog/which-tool-ai-meeting-notes)
- Fireflies — [Notepad guide](https://guide.fireflies.ai/articles/6653885315-learn-about-the-fireflies-notepad) · [desktop app guide](https://guide.fireflies.ai/articles/1208704416-getting-started-with-the-fireflies-desktop-app) · [2024 UI update](https://fireflies.ai/blog/fireflies-notepad-updates/)
- Otter — [conversation page overview](https://help.otter.ai/hc/en-us/articles/5093228433687-Conversation-Page-Overview)† · [PCMag review](https://www.pcmag.com/reviews/otter)†
- Granola — [product](https://www.granola.ai/ai-note-taker) · [Proser review](https://zackproser.com/blog/granola-ai-review) · [Wee review](https://celinewee.medium.com/granola-ai-notepad-177e86eb8d3c)† · [max-productive](https://max-productive.ai/ai-tools/granola/)
- Fathom — [BlueDot review](https://www.bluedothq.com/blog/fathom-review) · [usage guide](https://www.fahimai.com/how-to-use-fathom-ai) · [tl;dv critique](https://tldv.io/blog/honest-review-of-fathom/)
- Meetily — [GitHub](https://github.com/Zackriya-Solutions/meetily) · [site](https://meetily.ai/)
- Hyprnote / Anarlog — [site](https://anarlog.so/) · [FYI Combinator](https://fyicombinator.com/company/hyprnote) · [self-hosted roundup](https://anarlog.so/blog/selfhosted-ai-notetakers/)
- RTL — [Microsoft bidirectional support](https://learn.microsoft.com/en-us/dynamics365/fin-ops-core/dev-itpro/user-interface/bidirectional-support) · [Hebrew/Arabic RTL localisation](https://www.txl.co.il/post/hebrew-arabic-rtl-localization-design-challenges-and-how-to-solve-them)
