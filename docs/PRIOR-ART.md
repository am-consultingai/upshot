# Prior art — what already exists (scanned 2026-08-27)

Question: is someone already selling this, and is someone already giving it away?
Short answer: **yes to both — but neither does Hebrew properly, and neither does the
calendar→record→email loop end to end without paying.**

---

## 1. Paid products

All cloud. All subscription. Two capture styles: a **bot** that joins the call as a
participant, or **desktop capture** (bot-free) like what we're building.

| Product | Price (individual) | Capture | Notes |
|---|---|---|---|
| **Granola** | Free tier (limited history); paid from ~$14/user/mo (listed elsewhere at $18/mo, 25 free meetings lifetime) | Bot-free desktop | The 2026 darling. **Hebrew is not on its supported-language list** — En/Fr/De/Es/It/Pt/Nl/Ja/Ru/Hi, plus more on mobile only |
| **Circleback** | $25/mo, $20.83/mo annual | Bot-free desktop | Strong action-item extraction |
| **Otter.ai** | From $8.33/mo annual (Pro) | Bot + desktop (Enterprise, Oct 2025) | Cheapest of the majors. Hebrew-language reviews call its Hebrew "reasonable", not good |
| **Fireflies** | ~$10–19/user/mo; free tier capped at 800 min storage | Bot | **117 languages incl. Hebrew (`he`)**, same coverage for summaries; multi-language mode for 60+ |
| **Fathom** | Most generous free tier (unlimited recordings, 5 AI summaries/mo) | Bot-free desktop | Best free option to try |
| **tl;dv** | Freemium | Bot | Reviews report accuracy and speaker/language-detection problems |
| **Zoom AI Companion / Teams Copilot** | Bundled with existing licences | Native | Zero setup if you already pay for the platform; no control over model or prompt |

**The Hebrew picture:** Fireflies is the only major that clearly claims Hebrew. Granola
— the one everyone recommends — doesn't support it at all. None of them use the
**ivrit.ai** fine-tunes, which are the current state of the art for Hebrew ASR (there's
a [Hebrew transcription leaderboard](https://huggingface.co/spaces/ivrit-ai/hebrew-transcription-leaderboard)
tracking exactly this). A generic multilingual Whisper on Hebrew is measurably worse
than the fine-tune we already have downloaded.

**Israeli/Hebrew-market tools** (Tactiq, TimeOS, תמלולית, Tamleli Pro) are mostly
Chrome extensions or upload-and-transcribe services wrapping the same cloud engines.

---

## 2. Open source

| Project | Stars | License | Platform | Status |
|---|---|---|---|---|
| **[Meetily](https://github.com/Zackriya-Solutions/meetily)** (Zackriya) | **29,956** ⭐ / 3,206 forks | MIT | macOS + Windows + Linux | Very active (pushed today). Rust + Tauri + Next.js. whisper.cpp / Parakeet, speaker diarization, Ollama / Claude / Groq / OpenRouter summarization, 100% local |
| **[anarlog](https://github.com/fastrepl/anarlog)** (ex-Hyprnote) | 9,183 ⭐ | MIT | macOS-first, Windows + Linux builds exist | Very active. Tauri/Rust/React. Local Whisper w/ CUDA, BYO LLM key. **Brand split in 2026:** OSS renamed `anarlog`, hyprnote.com became char.com (cloud) |
| **[Vexa](https://github.com/Vexa-ai/vexa)** | 2,719 ⭐ | Apache-2.0 | Server/Docker | Active. Different model — **bots that auto-join** Meet/Teams/Zoom, real-time WebSocket transcripts, MCP server. Self-host, no audio leaves your VPC |
| **[Amurex](https://github.com/thepersonalaicompany/amurex)** | 2,868 ⭐ | AGPL-3.0 | Server + extension | **Stale — last push May 2025.** Notable because it's the only one that does follow-up email generation |
| Long tail | <500 ⭐ each | mixed | mixed | Dozens of single-author projects (VoiceFlow 413⭐ Win/Linux, MeetingBro, Scripta, Cenario, echonote…). Nothing to build on |

Hebrew-specific OSS is **transcription only**, no meeting automation:
[Vibe](https://github.com/thewh1teagle/vibe) (desktop transcription, ivrit.ai models supported),
HebrewScribe, [hebrew_whisper](https://github.com/ShmuelRonen/hebrew_whisper).

---

## 3. The gap analysis — what nobody has

Meetily is the closest thing to our design, and it's excellent. Here is precisely where
it stops short of the spec:

| Our requirement | Meetily | anarlog | Any paid tool |
|---|---|---|---|
| Local Whisper on my GPU | ✅ | ✅ | ❌ (all cloud) |
| **Custom fine-tuned model (ivrit.ai Hebrew)** | ❌ **not supported** — open FRs [#493](https://github.com/Zackriya-Solutions/meetily/issues/493), [#571](https://github.com/Zackriya-Solutions/meetily/issues/571) and open PRs [#506](https://github.com/Zackriya-Solutions/meetily/pull/506), [#669](https://github.com/Zackriya-Solutions/meetily/pull/669) since May 2026, unmerged | unclear/undocumented | ❌ nobody uses ivrit.ai |
| **Glossary / `initial_prompt` vocabulary bias** | ❌ open FR [#474](https://github.com/Zackriya-Solutions/meetily/issues/474) | ❌ | partial (custom vocab in some) |
| **Calendar-armed auto-record** | ❌ "Coming Soon", **PRO only** | ❌ **Pro tier**, $15/mo | ✅ (that's what you pay for) |
| **Email the summary** | ❌ (exports are PRO) | ❌ (shareable links are Pro) | ✅ |
| Hebrew RTL output template | ❌ | ❌ | partial |
| Two-track me/them separation | diarization instead | manual speaker labels | varies |

Both leading OSS projects have adopted the same commercial pattern: **the local
recording engine is free, the automation layer (calendar, integrations, export) is the
paid tier.** The automation layer is exactly the part we want.

ivrit.ai *does* publish GGML builds
([whisper-large-v3-ggml](https://huggingface.co/ivrit-ai/whisper-large-v3-ggml),
`whisper-v2-d4-ggml`), so Meetily's whisper.cpp engine *could* load a Hebrew fine-tune —
the plumbing to point it at a custom model file just isn't merged yet. Note also
whisper.cpp [issue #2052](https://github.com/ggml-org/whisper.cpp/issues/2052): it fails
to load models whose **path contains Hebrew characters**. A hint about how much Hebrew
testing these tools have had.

---

## 4. Cost of building vs. buying

Per 45-minute Hebrew meeting: ~15K input tokens, ~2K output.

| | Per meeting | 80 meetings/mo |
|---|---|---|
| Claude Sonnet 5 ($2 / $10 per MTok) | ~$0.05 | **~$4/mo** |
| Claude Opus 5 ($5 / $25 per MTok) | ~$0.13 | **~$10/mo** |
| Granola | — | $14–18/mo |
| Fireflies | — | $10–19/mo |
| Circleback | — | $21–25/mo |

Transcription is free (local GPU). So DIY on Sonnet 5 is ~4× cheaper than the cheapest
comparable subscription; on Opus 5 it's roughly break-even with Granola.

**Cost is therefore not the reason to build this.** The reasons are Hebrew quality,
audio never leaving the machine, and owning the prompts and the output format.

---

## 5. Recommendation

**Test-drive before you build — one evening, not a week.**

1. Install **Meetily** on Windows (free, MIT, one-click installer). Run it on one real Hebrew meeting.
2. Compare its transcript against the same audio through your existing ivrit.ai + faster-whisper script.

That comparison decides everything:

- **If generic Whisper's Hebrew is good enough** — adopt Meetily as the engine and build only the thin missing layer around it (calendar arming + email), or contribute the custom-model PR upstream. Much less code to own.
- **If the ivrit fine-tune is clearly better** (the likely outcome — it's why ivrit.ai exists) — build M1 as designed. You'd then be building the one thing that genuinely doesn't exist: a Hebrew-first, calendar-armed, local meeting agent. And you'd have proof rather than an assumption behind the decision.

Either way, steal from the field:
- **Meetily**: Parakeet as a faster ASR option; Ollama/Claude/Groq as swappable summarizers; diarization already wired.
- **anarlog**: the notepad model — your own notes typed during the call get merged with the transcript, which reportedly beats a pure auto-summary.
- **Amurex**: follow-up email generation as a first-class output (it's the only OSS project that did this, and it's dead — no competition).
- **Vexa**: if you ever want capture to work with your laptop closed, a self-hosted joining bot is the architecture for that.

## Sources
- [Meetily (GitHub)](https://github.com/Zackriya-Solutions/meetily) · [anarlog (GitHub)](https://github.com/fastrepl/anarlog) · [Vexa (GitHub)](https://github.com/Vexa-ai/vexa) · [Amurex (GitHub)](https://github.com/thepersonalaicompany/amurex)
- [Hyprnote vs Meetily comparison](https://openalternative.co/compare/hyprnote/vs/meetily) · [Best self-hosted AI notetakers](https://anarlog.so/blog/selfhosted-ai-notetakers/) · [anarlog.so](https://anarlog.so/)
- [Granola multi-language docs](https://docs.granola.ai/help-center/customising-granola/multi-language) · [Granola vs Fireflies vs Fathom vs Otter pricing](https://www.granola.ai/blog/meeting-note-tool-pricing-granola-vs-fireflies-fathom-otter) · [Fireflies supported languages](https://guide.fireflies.ai/articles/2973706448-learn-about-fireflies-supported-languages) · [Circleback: best AI meeting assistants](https://circleback.ai/blog/best-ai-meeting-assistants)
- [ivrit.ai](https://www.ivrit.ai/en/ivrit-ai-2/) · [Hebrew transcription leaderboard](https://huggingface.co/spaces/ivrit-ai/hebrew-transcription-leaderboard) · [ivrit-ai/whisper-large-v3-ggml](https://huggingface.co/ivrit-ai/whisper-large-v3-ggml) · [Vibe + ivrit.ai discussion](https://github.com/thewh1teagle/vibe/discussions/27)
- [כלכליסט: סיכום פגישות עם AI](https://www.calcalist.co.il/calcalistech/article/sjodkaotke) · [Forbes Israel: 4 שירותי AI לסיכום מפגשי זום](https://forbes.co.il/4-ai-services-for-summarizing-meetings/)
