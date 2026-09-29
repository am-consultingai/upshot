"""Every language Whisper transcribes: its code, its English name and its own name.

The codes are faster-whisper's (``faster_whisper.tokenizer._LANGUAGE_CODES``); a test
checks that this table covers every one. The English name tells the summarizer which
language to write the notes in ("Spanish (es)"); the native name is what the hidden
"Transcribe again as…" list shows.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name, "native": self.native}


_TABLE: tuple[tuple[str, str, str], ...] = (
    ("af", "Afrikaans", "Afrikaans"),
    ("am", "Amharic", "አማርኛ"),
    ("ar", "Arabic", "العربية"),
    ("as", "Assamese", "অসমীয়া"),
    ("az", "Azerbaijani", "Azərbaycanca"),
    ("ba", "Bashkir", "Башҡортса"),
    ("be", "Belarusian", "Беларуская"),
    ("bg", "Bulgarian", "Български"),
    ("bn", "Bengali", "বাংলা"),
    ("bo", "Tibetan", "བོད་སྐད"),
    ("br", "Breton", "Brezhoneg"),
    ("bs", "Bosnian", "Bosanski"),
    ("ca", "Catalan", "Català"),
    ("cs", "Czech", "Čeština"),
    ("cy", "Welsh", "Cymraeg"),
    ("da", "Danish", "Dansk"),
    ("de", "German", "Deutsch"),
    ("el", "Greek", "Ελληνικά"),
    ("en", "English", "English"),
    ("es", "Spanish", "Español"),
    ("et", "Estonian", "Eesti"),
    ("eu", "Basque", "Euskara"),
    ("fa", "Persian", "فارسی"),
    ("fi", "Finnish", "Suomi"),
    ("fo", "Faroese", "Føroyskt"),
    ("fr", "French", "Français"),
    ("gl", "Galician", "Galego"),
    ("gu", "Gujarati", "ગુજરાતી"),
    ("ha", "Hausa", "Hausa"),
    ("haw", "Hawaiian", "ʻŌlelo Hawaiʻi"),
    ("he", "Hebrew", "עברית"),
    ("hi", "Hindi", "हिन्दी"),
    ("hr", "Croatian", "Hrvatski"),
    ("ht", "Haitian Creole", "Kreyòl ayisyen"),
    ("hu", "Hungarian", "Magyar"),
    ("hy", "Armenian", "Հայերեն"),
    ("id", "Indonesian", "Bahasa Indonesia"),
    ("is", "Icelandic", "Íslenska"),
    ("it", "Italian", "Italiano"),
    ("ja", "Japanese", "日本語"),
    ("jw", "Javanese", "Basa Jawa"),
    ("ka", "Georgian", "ქართული"),
    ("kk", "Kazakh", "Қазақша"),
    ("km", "Khmer", "ខ្មែរ"),
    ("kn", "Kannada", "ಕನ್ನಡ"),
    ("ko", "Korean", "한국어"),
    ("la", "Latin", "Latina"),
    ("lb", "Luxembourgish", "Lëtzebuergesch"),
    ("ln", "Lingala", "Lingála"),
    ("lo", "Lao", "ລາວ"),
    ("lt", "Lithuanian", "Lietuvių"),
    ("lv", "Latvian", "Latviešu"),
    ("mg", "Malagasy", "Malagasy"),
    ("mi", "Maori", "Te reo Māori"),
    ("mk", "Macedonian", "Македонски"),
    ("ml", "Malayalam", "മലയാളം"),
    ("mn", "Mongolian", "Монгол"),
    ("mr", "Marathi", "मराठी"),
    ("ms", "Malay", "Bahasa Melayu"),
    ("mt", "Maltese", "Malti"),
    ("my", "Burmese", "မြန်မာ"),
    ("ne", "Nepali", "नेपाली"),
    ("nl", "Dutch", "Nederlands"),
    ("nn", "Norwegian Nynorsk", "Nynorsk"),
    ("no", "Norwegian", "Norsk"),
    ("oc", "Occitan", "Occitan"),
    ("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    ("pl", "Polish", "Polski"),
    ("ps", "Pashto", "پښتو"),
    ("pt", "Portuguese", "Português"),
    ("ro", "Romanian", "Română"),
    ("ru", "Russian", "Русский"),
    ("sa", "Sanskrit", "संस्कृतम्"),
    ("sd", "Sindhi", "سنڌي"),
    ("si", "Sinhala", "සිංහල"),
    ("sk", "Slovak", "Slovenčina"),
    ("sl", "Slovenian", "Slovenščina"),
    ("sn", "Shona", "chiShona"),
    ("so", "Somali", "Soomaali"),
    ("sq", "Albanian", "Shqip"),
    ("sr", "Serbian", "Српски"),
    ("su", "Sundanese", "Basa Sunda"),
    ("sv", "Swedish", "Svenska"),
    ("sw", "Swahili", "Kiswahili"),
    ("ta", "Tamil", "தமிழ்"),
    ("te", "Telugu", "తెలుగు"),
    ("tg", "Tajik", "Тоҷикӣ"),
    ("th", "Thai", "ไทย"),
    ("tk", "Turkmen", "Türkmençe"),
    ("tl", "Tagalog", "Tagalog"),
    ("tr", "Turkish", "Türkçe"),
    ("tt", "Tatar", "Татарча"),
    ("uk", "Ukrainian", "Українська"),
    ("ur", "Urdu", "اردو"),
    ("uz", "Uzbek", "Oʻzbekcha"),
    ("vi", "Vietnamese", "Tiếng Việt"),
    ("yi", "Yiddish", "ייִדיש"),
    ("yo", "Yoruba", "Yorùbá"),
    ("zh", "Chinese", "中文"),
    ("yue", "Cantonese", "粵語"),
)

LANGUAGES: dict[str, Language] = {
    code: Language(code, name, native) for code, name, native in _TABLE
}

#: Written right to left: the page and the transcript turn round for these.
RTL_LANGUAGES = frozenset({"he", "ar", "fa", "ur", "yi", "ps", "sd"})


def base(code: str) -> str:
    """``pt-BR`` → ``pt``: the part Whisper and these tables know."""
    return code.split("-")[0].split("_")[0].lower()


def is_supported(code: str) -> bool:
    return code in LANGUAGES


def english_name(code: str) -> str:
    """ "Spanish" for ``es``; an unknown code is its own name."""
    known = LANGUAGES.get(base(code))
    return known.name if known else code


def direction_for(code: str | None) -> str:
    return "rtl" if code and base(code) in RTL_LANGUAGES else "ltr"
