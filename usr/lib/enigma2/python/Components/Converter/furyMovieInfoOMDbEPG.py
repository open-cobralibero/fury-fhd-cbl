# -*- coding: utf-8 -*-
from Components.Converter.Converter import Converter
from Components.Element import cached

import json
import os
import re
import threading
import time
import traceback
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote
from urllib.request import Request, urlopen

# -----------------------------------------------------------------------------
# Fury Auto Movie Info (EPG Selected Event / Now Event)
# -----------------------------------------------------------------------------

# ======================================================
# ضع مفاتيحك هنا (كما في ملفك)
# ======================================================
TMDB_API_KEY = "a73256be6d80f7b7d7448673a6ff24ee"
OMDB_API_KEY = "e12396d4"
# ======================================================

# -----------------------------
# Optional: AIFury translator
# -----------------------------
try:
    from Components.config import config
    from Plugins.Extensions.AIFury.plugin import AIFuryController
except Exception:
    config = None
    AIFuryController = None

try:
    from enigma import eTimer
except Exception:
    eTimer = None

# -----------------------------
# Tunables
# -----------------------------
TIMEOUT = 4
CACHE_SECONDS = 7200            # 2 hours
DEBOUNCE_SECONDS = 0.35         # أسرع شوية عشان "أول ما أقف على الحدث"
POLL_MS = 600                   # تحديث UI أثناء الجلب
DEBUG_LOG = "/tmp/fury_movieinfo_auto.log"
MAX_QUERY_VARIANTS = 3      # لا نزود عدد المحاولات حتى لا يحصل ثقل
MIN_MATCH_SCORE = 35.0      # حد أدنى مرن لتجنب الماتشات العشوائية جدًا

# Poster base
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/"
USER_AGENT = "Enigma2-FuryMovieInfo/1.2"
ALIAS_FILE = "/etc/enigma2/yw_aliases.json"

# Disk cache for AIFury
_DESC_CACHE_FILE = "desc_cache.json"


# -----------------------------
# Logging
# -----------------------------
def _log(msg):
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# -----------------------------
# Helpers
# -----------------------------
def _safe(v, fallback="—"):
    if v is None:
        return fallback
    try:
        s = str(v).strip()
    except Exception:
        return fallback
    return s if s else fallback


def _fmt_rating(r):
    # r could be float or string
    if r is None:
        return "—/10"
    try:
        rr = float(str(r).strip())
        rr = max(0.0, min(10.0, rr))
        return "%0.1f/10" % rr
    except Exception:
        return "—/10"


def _fmt_runtime(rt):
    if rt is None:
        return "—"
    if isinstance(rt, int):
        return "%d min" % rt
    s = str(rt).strip()
    if not s or s.lower() in ("n/a", "na"):
        return "—"
    return s


def _norm_title(s):
    if not s:
        return ""
    s = s.lower()
    # remove brackets content & punctuation-ish
    s = re.sub(r"\(.*?\)|\[.*?\]|\{.*?\}", " ", s)
    s = re.sub(r"[^0-9a-z\u0600-\u06FF\u0400-\u04FF]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _similarity(a, b):
    a = _norm_title(a)
    b = _norm_title(b)
    if not a or not b:
        return 0.0
    try:
        return SequenceMatcher(None, a, b).ratio()
    except Exception:
        return 0.0


def _ascii_fold(text):
    if not text:
        return ""
    try:
        s = unicodedata.normalize("NFKD", str(text))
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
    except Exception:
        s = str(text)
    try:
        s = s.encode("ascii", "ignore").decode("ascii", errors="ignore")
    except Exception:
        pass
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _contains_non_ascii(text):
    if not text:
        return False
    try:
        for ch in str(text):
            if ord(ch) > 127:
                return True
    except Exception:
        return False
    return False


def _detect_kind_hint(raw_title):
    t = (raw_title or "").strip()
    if not t:
        return ""
    checks = [
        r"\bS\d{1,2}E\d{1,3}\b",
        r"\bE\d{1,3}\b",
        r"\bEP\s*\d{1,3}\b",
        r"\bSeason\s*\d{1,2}\b",
        r"\bEpisode\s*\d{1,3}\b",
        r"\bPart\s*\d{1,2}\b",
        r"\bالموسم\s*\d{1,2}\b",
        r"\bالحلقة\s*\d{1,3}\b",
    ]
    for pat in checks:
        try:
            if re.search(pat, t, flags=re.IGNORECASE):
                return "tv"
        except Exception:
            continue
    low = t.lower()
    if ("مسلسل" in t) or ("حلقة" in t) or (" season " in (" " + low + " ")) or (" episode " in (" " + low + " ")):
        return "tv"
    return ""


def _make_cache_key(clean, y_hint, raw_title):
    kind = _detect_kind_hint(raw_title) or "auto"
    return (kind + "|" + (clean or "") + "|" + (y_hint or "")).lower()


def _detect_lang_hint(text):
    # Arabic / Russian detection, else English
    if not text:
        return "en"
    for ch in text:
        o = ord(ch)
        if (0x0600 <= o <= 0x06FF) or (0x0750 <= o <= 0x077F) or (0x08A0 <= o <= 0x08FF):
            return "ar"
        if 0x0400 <= o <= 0x04FF:
            return "ru"
    return "en"


def _clean_title(title):
    """Return (clean_title, year_hint)."""
    t = (title or "").strip()
    if not t:
        return "", ""

    # year hint
    y = ""
    m = re.search(r"\((19\d{2}|20\d{2})\)", t)
    if m:
        y = m.group(1)
        t = re.sub(r"\((19\d{2}|20\d{2})\)", "", t).strip()
    else:
        m2 = re.search(r"\b(19\d{2}|20\d{2})\b", t)
        if m2:
            y = m2.group(1)
            t = re.sub(r"\b(19\d{2}|20\d{2})\b", "", t).strip(" -_|")

    # common junk tokens
    junk = [
        r"\bHD\b", r"\bFHD\b", r"\bUHD\b", r"\b4K\b",
        r"\b1080p\b", r"\b720p\b", r"\bH\.264\b", r"\bHEVC\b",
        r"\bWEB\b", r"\bDL\b", r"\bBluRay\b",
        r"^Movie\s*:\s*", r"^Movie\s+",
        r"^Film\s*:\s*", r"^Film\s+",
        r"^فيلم\s*:\s*", r"^فيلم\s+",
        r"^مسلسل\s*:\s*", r"^مسلسل\s+",
        r"\[.*?\]",
        # episodes patterns (خفيفة بدون مبالغة)
        r"\bS\d{1,2}E\d{1,2}\b", r"\bE\d{1,3}\b", r"\bEP\s*\d{1,3}\b",
        r"\bSeason\s*\d{1,2}\b", r"\bEpisode\s*\d{1,3}\b",
        r"\bالحلقة\s*\d{1,3}\b",
    ]
    for pat in junk:
        t = re.sub(pat, "", t, flags=re.IGNORECASE).strip()

    t = t.strip(" -_|:·•")
    return t, y


def _http_json(url):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _tmdb_poster_url(poster_path, size="w342"):
    if not poster_path:
        return ""
    p = str(poster_path).strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return "%s%s%s" % (TMDB_IMAGE_BASE, size, p)


def _join_names(names, limit=10):
    out = []
    seen = set()
    for n in names or []:
        if not n:
            continue
        s = str(n).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if limit and len(out) >= limit:
            break
    return ", ".join(out)


def _event_desc(ev):
    """Fallback EPG description (extended -> short)."""
    if not ev:
        return ""
    try:
        d = (ev.getExtendedDescription() or "").strip()
        if d:
            return d
    except Exception:
        pass
    try:
        d = (ev.getShortDescription() or "").strip()
        return d
    except Exception:
        return ""


def _event_alt_title(ev):
    if not ev:
        return ""
    try:
        sd = (ev.getShortDescription() or "").strip()
    except Exception:
        sd = ""
    if not sd or len(sd) >= 60:
        return ""
    try:
        alt, _ = _clean_title(sd)
        return _check_alias(alt)
    except Exception:
        return ""


def _is_arabic_text(text):
    try:
        return bool(re.search(r'[؀-ۿ]', text or ""))
    except Exception:
        return False


def _legacy_title_similarity(a, b):
    if not a or not b:
        return 0.0
    try:
        sa = set(_norm_title(a).split())
        sb = set(_norm_title(b).split())
        if not sa or not sb:
            return 0.0
        return float(len(sa.intersection(sb))) / float(max(len(sa), len(sb)))
    except Exception:
        return 0.0


def _check_alias(title):
    t = (title or "").strip()
    if not t:
        return ""
    try:
        if not os.path.exists(ALIAS_FILE):
            default_aliases = {
                "Bagazhi i dorës": "Carry-On",
                "Dita e vendimit": "Draft Day",
                "E ligë Për mirë": "Wicked",
            }
            try:
                with open(ALIAS_FILE, "w", encoding="utf-8") as f:
                    json.dump(default_aliases, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        with open(ALIAS_FILE, "r", encoding="utf-8") as f:
            aliases = json.load(f)
        title_lower = t.lower()
        for k, v in (aliases or {}).items():
            if (str(k or "").strip().lower() == title_lower) and v:
                mapped = str(v).strip()
                if mapped:
                    _log('ALIAS "%s" -> "%s"' % (t, mapped))
                    return mapped
    except Exception:
        pass
    return t


def _groq_deduce_title(title):
    query = (title or "").strip()
    if not query:
        return None
    key_path = "/etc/enigma2/groq_api_key.txt"
    try:
        if not os.path.exists(key_path):
            return None
        with open(key_path, "r", encoding="utf-8") as f:
            key = (f.read() or "").strip()
        if not key:
            return None
        import ssl
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {
                    "role": "system",
                    "content": "You identify the exact official English movie or TV title from poor or literal translations. Return only the title.",
                },
                {
                    "role": "user",
                    "content": query,
                },
            ],
            "temperature": 0.1,
        }
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        resp = urlopen(req, context=ctx, timeout=TIMEOUT)
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        value = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if value and len(value) < 80 and value.lower() != query.lower():
            _log('GROQ "%s" -> "%s"' % (query, value))
            return value
    except Exception:
        pass
    return None


def _translate_text_via_mymemory(text, langpair):
    query = (text or "").strip()
    pair = (langpair or "").strip()
    if not query or not pair:
        return ""
    try:
        url = "https://api.mymemory.translated.net/get?q=%s&langpair=%s" % (quote(query, safe=""), quote(pair, safe=""))
        req = Request(url, headers={"User-Agent": USER_AGENT})
        resp = urlopen(req, timeout=TIMEOUT)
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        translated = (((data.get("responseData") or {}).get("translatedText") or "").strip())
        if translated and translated.lower() != query.lower() and "MYMEMORY WARNING" not in translated.upper():
            return translated
    except Exception:
        pass
    return ""


def _build_legacy_tmdb_passes(search_title, alt_title=""):
    passes = []
    seen = set()

    def add_pass(query, lang=""):
        q = re.sub(r"\s+", " ", str(query or "")).strip()
        l = str(lang or "").strip()
        if not q:
            return
        key = (q.lower(), l.lower())
        if key in seen:
            return
        seen.add(key)
        passes.append((q, l))

    def add_query_family(query):
        q = (query or "").strip()
        if not q:
            return
        is_arabic = _is_arabic_text(q)
        if is_arabic:
            base = [q]
            if "الا" in q:
                base.append(q.replace("الا", "الأ"))
            if q.startswith("ا") and len(q) > 1:
                base.append("أ" + q[1:])
            for item in base:
                add_pass(item, "ar-AE")
                add_pass(item, "")
        else:
            add_pass(q, "sq-AL")
            add_pass(q, "")

    add_query_family(search_title)
    if alt_title and alt_title.lower() != (search_title or "").lower():
        add_query_family(alt_title)
    return passes


def _pick_legacy_tmdb_result(search_title, results, is_arabic):
    if not results:
        return None, (search_title or "").strip()
    try:
        ordered = sorted(results, key=lambda x: float(x.get("popularity") or 0.0), reverse=True)
    except Exception:
        ordered = list(results)

    item = None
    for r in ordered:
        if r.get("backdrop_path"):
            item = r
            break
    if item is None and ordered:
        item = ordered[0]
    if not item:
        return None, (search_title or "").strip()

    extracted_title = (
        item.get("title")
        or item.get("name")
        or item.get("original_title")
        or item.get("original_name")
        or search_title
        or ""
    ).strip()

    if is_arabic and _is_arabic_text(extracted_title):
        if _legacy_title_similarity(search_title, extracted_title) < 0.3:
            _log('TMDB REJECT "%s" != "%s"' % (search_title, extracted_title))
            return None, (search_title or "").strip()

    return item, extracted_title or (search_title or "").strip()


# -----------------------------
# AIFury translation cache
# -----------------------------
def _aifury_is_enabled():
    try:
        if config is None:
            return False
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enabled"):
            return bool(config.plugins.aifury.enabled.value)
        return True
    except Exception:
        return False


def _aifury_get_lang():
    try:
        if config is None:
            return ""
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "language"):
            return (config.plugins.aifury.language.value or "").strip()
    except Exception:
        pass
    return ""


def _aifury_get_controller():
    if not _aifury_is_enabled():
        return None
    try:
        if AIFuryController is None:
            return None
        inst = getattr(AIFuryController, "instance", None)
        if callable(inst):
            inst = inst()
        return inst
    except Exception:
        return None


class _DescDiskCache:
    _lock = threading.RLock()
    _loaded = False
    _loading = False
    _data = {}
    _dirty = False
    _last_flush = 0.0

    @staticmethod
    def _pick_base_dir():
        candidates = ["/media/hdd/AIFury", "/media/usb/AIFury"]
        try:
            if config is not None and hasattr(config, "plugins") and hasattr(config.plugins, "aifury"):
                cp = getattr(config.plugins.aifury, "cachepath", None)
                if cp is not None:
                    cpv = (cp.value or "").strip()
                    if cpv and cpv.lower() != "no path":
                        candidates.append(cpv.rstrip("/"))
        except Exception:
            pass
        candidates += ["/tmp/AIFury"]

        for d in candidates:
            try:
                os.makedirs(d, exist_ok=True)
                test = os.path.join(d, ".aifury_desc_write_test")
                with open(test, "w") as f:
                    f.write("1")
                try:
                    os.remove(test)
                except Exception:
                    pass
                return d
            except Exception:
                continue
        return "/tmp/AIFury"

    @classmethod
    def _cache_path(cls):
        base = cls._pick_base_dir()
        return os.path.join(base, _DESC_CACHE_FILE)

    @classmethod
    def _load_worker(cls):
        path = cls._cache_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                with cls._lock:
                    cls._data = obj
        except Exception:
            pass
        with cls._lock:
            cls._loaded = True
            cls._loading = False

    @classmethod
    def ensure_loaded_async(cls):
        with cls._lock:
            if cls._loaded or cls._loading:
                return
            cls._loading = True
        try:
            threading.Thread(target=cls._load_worker, daemon=True).start()
        except Exception:
            with cls._lock:
                cls._loaded = True
                cls._loading = False

    @classmethod
    def get(cls, key):
        if not cls._loaded:
            cls.ensure_loaded_async()
            return None
        with cls._lock:
            return cls._data.get(key)

    @classmethod
    def set(cls, key, value):
        if not key or not value:
            return
        try:
            if len(value) > 12000:
                return
        except Exception:
            pass
        with cls._lock:
            cls._data[key] = value
            cls._dirty = True
        cls._flush_async_debounced()

    @classmethod
    def _flush_worker(cls):
        with cls._lock:
            if not cls._dirty:
                return
            data = dict(cls._data)
            cls._dirty = False
        path = cls._cache_path()
        tmp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            with cls._lock:
                cls._dirty = True
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    @classmethod
    def _flush_async_debounced(cls):
        now = time.time()
        with cls._lock:
            if now - cls._last_flush < 2.0:
                return
            cls._last_flush = now
        try:
            threading.Thread(target=cls._flush_worker, daemon=True).start()
        except Exception:
            pass


def _aifury_translate_desc_cached_or_async(text):
    if not text:
        return text
    norm = str(text).strip()
    if not norm:
        return text

    lang = _aifury_get_lang()
    key = (lang + "|" + norm).lower()

    try:
        cached = _DescDiskCache.get(key)
        if cached:
            return cached
    except Exception:
        pass

    ctrl = _aifury_get_controller()
    if ctrl is None:
        return text

    out = None
    try:
        if hasattr(ctrl, "translate_cached_or_async"):
            out = ctrl.translate_cached_or_async(norm)
    except Exception:
        out = None

    try:
        if isinstance(out, str):
            o = out.strip()
            if o and o != norm:
                _DescDiskCache.set(key, o)
                return o
    except Exception:
        pass

    return out if isinstance(out, str) and out else text


def _aifury_translate_to_english_cached_or_async(text):
    if not text:
        return text
    norm = str(text).strip()
    if not norm:
        return text

    # For English/ASCII titles there is no need to do extra work.
    if _detect_lang_hint(norm) == "en" and not _contains_non_ascii(norm):
        return norm

    key = ("title_en|" + norm).lower()

    try:
        cached = _DescDiskCache.get(key)
        if cached:
            return cached
    except Exception:
        pass

    ctrl = _aifury_get_controller()
    if ctrl is None:
        return norm

    out = None

    # Try explicit target-language APIs first if the controller exposes one.
    for fn_name in (
        "translate_to_lang_cached_or_async",
        "translate_text_cached_or_async",
        "translate_to_language_cached_or_async",
    ):
        try:
            fn = getattr(ctrl, fn_name, None)
            if callable(fn):
                out = fn(norm, "en")
                if isinstance(out, str) and out.strip() and out.strip() != norm:
                    break
        except Exception:
            out = None

    # Fallback to the existing method only when AIFury target language is English.
    if not (isinstance(out, str) and out.strip() and out.strip() != norm):
        try:
            current_lang = (_aifury_get_lang() or "").strip().lower()
            if current_lang.startswith("en"):
                fn = getattr(ctrl, "translate_cached_or_async", None)
                if callable(fn):
                    out = fn(norm)
        except Exception:
            out = None

    try:
        if isinstance(out, str):
            o = out.strip()
            if o and o != norm:
                _DescDiskCache.set(key, o)
                return o
    except Exception:
        pass

    return norm


def _build_query_variants(clean, raw_title):
    out = []
    seen = set()

    def add(value):
        try:
            c, _ = _clean_title(value)
        except Exception:
            c = str(value or "").strip()
        c = re.sub(r"\s+", " ", (c or "").strip())
        if not c:
            return
        if re.match(r"^[0-9]+$", c):
            return
        k = c.lower()
        if k in seen:
            return
        seen.add(k)
        out.append(c)

    add(clean)

    folded_clean = _ascii_fold(clean)
    if folded_clean and folded_clean.lower() != (clean or "").lower():
        add(folded_clean)

    needs_en_alias = (_detect_lang_hint(raw_title or clean) != "en") or _contains_non_ascii(raw_title or clean)
    if needs_en_alias:
        en_alias = _aifury_translate_to_english_cached_or_async(raw_title or clean)
        add(en_alias)
        folded_alias = _ascii_fold(en_alias)
        if folded_alias and folded_alias.lower() != (en_alias or "").lower():
            add(folded_alias)

    return out[:MAX_QUERY_VARIANTS]


# -----------------------------
# Genre map (TMDB)
# -----------------------------
class _GenreMap:
    lock = threading.Lock()
    maps = {}  # (kind, lang) -> (ts, {id: name})

    @classmethod
    def get(cls, kind, lang):
        now = int(time.time())
        key = ((kind or "movie"), (lang or "en"))
        with cls.lock:
            item = cls.maps.get(key)
            if item:
                ts, mp = item
                if (now - ts) < CACHE_SECONDS:
                    return mp
        return None

    @classmethod
    def set(cls, kind, lang, mp):
        now = int(time.time())
        key = ((kind or "movie"), (lang or "en"))
        with cls.lock:
            cls.maps[key] = (now, mp)

    @classmethod
    def ensure(cls, kind, lang):
        kind = "tv" if (kind or "").lower() == "tv" else "movie"
        lang = lang or "en"
        mp = cls.get(kind, lang)
        if mp is not None:
            return mp
        try:
            if not TMDB_API_KEY or TMDB_API_KEY.startswith("PUT_"):
                return {}
            url = "https://api.themoviedb.org/3/genre/%s/list?api_key=%s&language=%s" % (kind, TMDB_API_KEY, lang)
            data = _http_json(url)
            genres = data.get("genres") or []
            mp = {}
            for g in genres:
                gid = g.get("id")
                name = g.get("name")
                if gid and name:
                    mp[int(gid)] = str(name)
            cls.set(kind, lang, mp)
            return mp
        except Exception:
            return {}


# -----------------------------
# Async Cache with backoff
# -----------------------------
class _AsyncCache:
    lock = threading.Lock()
    # key -> {"ts": int, "status": "ready"|"neg"|"inflight", "data": dict|None, "attempts": int, "next_try": float}
    store = {}

    @classmethod
    def get(cls, key):
        now = int(time.time())
        with cls.lock:
            item = cls.store.get(key)
            if not item:
                return None
            if (now - item.get("ts", 0)) > CACHE_SECONDS:
                cls.store.pop(key, None)
                return None
            return item

    @classmethod
    def try_mark_inflight(cls, key):
        """True if allowed to start fetch now, False otherwise (ready/inflight/backoff)."""
        now = time.time()
        with cls.lock:
            item = cls.store.get(key)
            if not item:
                cls.store[key] = {"ts": int(now), "status": "inflight", "data": None, "attempts": 0, "next_try": 0.0}
                return True

            st = item.get("status")
            if st == "ready" and item.get("data"):
                return False
            if st == "inflight":
                return False

            nxt = float(item.get("next_try", 0.0) or 0.0)
            if now < nxt:
                return False

            item["ts"] = int(now)
            item["status"] = "inflight"
            item["data"] = None
            cls.store[key] = item
            return True

    @classmethod
    def set_ready(cls, key, data):
        now = int(time.time())
        with cls.lock:
            cls.store[key] = {"ts": now, "status": "ready", "data": data, "attempts": 0, "next_try": 0.0}

    @classmethod
    def set_neg(cls, key):
        now = time.time()
        with cls.lock:
            prev = cls.store.get(key) or {}
            attempts = int(prev.get("attempts", 0) or 0) + 1

            # backoff (2s,5s,10s,20s,30..60s)
            if attempts <= 1:
                delay = 2.0
            elif attempts == 2:
                delay = 5.0
            elif attempts == 3:
                delay = 10.0
            elif attempts == 4:
                delay = 20.0
            else:
                delay = min(60.0, 30.0 + (attempts - 5) * 5.0)

            cls.store[key] = {
                "ts": int(now),
                "status": "neg",
                "data": None,
                "attempts": attempts,
                "next_try": now + delay,
            }


# -----------------------------
# Fetchers
# -----------------------------
def _omdb_type_from_kind(kind_hint):
    k = (kind_hint or "").strip().lower()
    if k == "tv":
        return "series"
    if k == "movie":
        return "movie"
    return ""


def _omdb_fetch_by_title(clean, y_hint="", kind_hint=""):
    try:
        if not OMDB_API_KEY or OMDB_API_KEY.startswith("PUT_"):
            return None
        q = quote(clean)
        url = "https://www.omdbapi.com/?apikey=%s&t=%s&plot=short&r=json" % (OMDB_API_KEY, q)
        omdb_type = _omdb_type_from_kind(kind_hint)
        if omdb_type:
            url += "&type=%s" % quote(omdb_type)
        if y_hint:
            url += "&y=%s" % quote(y_hint)
        data = _http_json(url)
        if not isinstance(data, dict) or data.get("Response") != "True":
            return None
        return data
    except Exception:
        return None


def _omdb_search_by_title(clean, y_hint="", kind_hint=""):
    try:
        if not OMDB_API_KEY or OMDB_API_KEY.startswith("PUT_"):
            return []
        q = quote(clean)
        url = "https://www.omdbapi.com/?apikey=%s&s=%s&r=json" % (OMDB_API_KEY, q)
        omdb_type = _omdb_type_from_kind(kind_hint)
        if omdb_type:
            url += "&type=%s" % quote(omdb_type)
        if y_hint:
            url += "&y=%s" % quote(y_hint)
        data = _http_json(url)
        if not isinstance(data, dict) or data.get("Response") != "True":
            return []
        return data.get("Search") or []
    except Exception:
        return []


def _omdb_fetch_by_imdbid(imdb_id):
    try:
        if not OMDB_API_KEY or OMDB_API_KEY.startswith("PUT_"):
            return None
        if not imdb_id:
            return None
        url = "https://www.omdbapi.com/?apikey=%s&i=%s&plot=short&r=json" % (OMDB_API_KEY, quote(imdb_id))
        data = _http_json(url)
        if not isinstance(data, dict) or data.get("Response") != "True":
            return None
        return data
    except Exception:
        return None


def _omdb_result_score(query_variants, y_hint, item, kind_hint=""):
    try:
        title = item.get("Title") or ""
        sim = 0.0
        for q in query_variants or []:
            sim = max(sim, _similarity(q, title))
        score = sim * 100.0

        kind = (kind_hint or "").strip().lower()
        itype = (item.get("Type") or "").strip().lower()
        if kind == "tv":
            score += 10.0 if itype == "series" else -8.0
        elif kind == "movie":
            score += 8.0 if itype == "movie" else 0.0

        year = (item.get("Year") or "")[:4]
        if y_hint and year:
            if year == y_hint:
                score += 25.0
            else:
                try:
                    dy = abs(int(year) - int(y_hint))
                    score -= min(15.0, dy * 2.0)
                except Exception:
                    pass
        return score
    except Exception:
        return -1e9


def _pick_best_omdb_result(query_variants, y_hint, results, kind_hint=""):
    if not results:
        return None
    best = None
    best_score = -1e9
    for item in results:
        score = _omdb_result_score(query_variants, y_hint, item, kind_hint)
        if score > best_score:
            best_score = score
            best = item
    return best


def _omdb_fetch_best(query_variants, y_hint="", kind_hint=""):
    qv = []
    for q in query_variants or []:
        qq = (q or "").strip()
        if qq and qq not in qv:
            qv.append(qq)

    # Exact title lookup first (cheap and accurate when it works).
    for q in qv[:2]:
        om = _omdb_fetch_by_title(q, y_hint, kind_hint)
        if om:
            return om

    # Relaxed search only if exact title failed.
    for q in qv[:2]:
        results = _omdb_search_by_title(q, y_hint, kind_hint)
        best = _pick_best_omdb_result(qv, y_hint, results, kind_hint)
        imdb_id = (best or {}).get("imdbID")
        if imdb_id:
            om = _omdb_fetch_by_imdbid(imdb_id)
            if om:
                return om
    return None


def _tmdb_media_type(item):
    mt = (item.get("media_type") or "").strip().lower()
    if mt in ("movie", "tv"):
        return mt
    if (item.get("title") is not None) or (item.get("release_date") is not None):
        return "movie"
    if (item.get("name") is not None) or (item.get("first_air_date") is not None):
        return "tv"
    return ""


def _tmdb_title_variants(item):
    out = []
    seen = set()
    for key in ("title", "original_title", "name", "original_name"):
        try:
            val = (item.get(key) or "").strip()
        except Exception:
            val = ""
        if not val:
            continue
        low = val.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(val)
    return out


def _tmdb_result_year(item):
    rel = (item.get("release_date") or item.get("first_air_date") or "").strip()
    return rel[:4] if len(rel) >= 4 else ""


def _tmdb_result_score(query_variants, y_hint, item, kind_hint=""):
    try:
        titles = _tmdb_title_variants(item)
        if not titles:
            return -1e9

        sim = 0.0
        for q in query_variants or []:
            for t in titles:
                sim = max(sim, _similarity(q, t))
        score = sim * 100.0

        # exact/near-exact bonus
        try:
            nq = [_norm_title(q) for q in (query_variants or []) if q]
            nt = [_norm_title(t) for t in titles if t]
            for q in nq:
                for t in nt:
                    if q and t and q == t:
                        score += 18.0
                        raise StopIteration
        except StopIteration:
            pass
        except Exception:
            pass

        media_type = _tmdb_media_type(item)
        kind = (kind_hint or "").strip().lower()
        if kind in ("movie", "tv"):
            if media_type == kind:
                score += 10.0
            elif media_type:
                score -= 8.0

        year = _tmdb_result_year(item)
        if y_hint and year:
            if year == y_hint:
                score += 25.0
            else:
                try:
                    dy = abs(int(year) - int(y_hint))
                    score -= min(15.0, dy * 2.0)
                except Exception:
                    pass

        try:
            pop = float(item.get("popularity") or 0.0)
            score += min(10.0, pop / 20.0)
        except Exception:
            pass

        try:
            vc = float(item.get("vote_count") or 0.0)
            score += min(6.0, vc / 250.0)
        except Exception:
            pass

        return score
    except Exception:
        return -1e9


def _pick_best_tmdb_result(query_variants, y_hint, results, kind_hint=""):
    if not results:
        return None

    best = None
    best_score = -1e9
    for r in results:
        try:
            media_type = _tmdb_media_type(r)
            if media_type not in ("movie", "tv"):
                continue
            score = _tmdb_result_score(query_variants, y_hint, r, kind_hint)
            if score > best_score:
                best_score = score
                best = r
        except Exception:
            continue

    return best or results[0]


def _tmdb_search_multi(clean, lang=""):
    q = quote(clean)
    url = "https://api.themoviedb.org/3/search/multi?api_key=%s&query=%s&page=1&include_adult=false" % (TMDB_API_KEY, q)
    if lang:
        url += "&language=%s" % quote(lang)
    data = _http_json(url)
    out = []
    for item in (data.get("results") or []):
        mt = _tmdb_media_type(item)
        if mt in ("movie", "tv"):
            out.append(item)
    return out


def _tmdb_details(tmdb_id, lang, media_type="movie"):
    media_type = "tv" if (media_type or "").lower() == "tv" else "movie"
    url = "https://api.themoviedb.org/3/%s/%s?api_key=%s&language=%s&append_to_response=credits,external_ids" % (
        media_type,
        str(int(tmdb_id)),
        TMDB_API_KEY,
        lang or "en",
    )
    return _http_json(url)


def _fill_from_omdb(out, om, clean):
    if not isinstance(om, dict):
        return out

    try:
        ir = (om.get("imdbRating") or "").strip()
        if ir and ir.lower() != "n/a":
            out["imdb_rating"] = float(ir)
    except Exception:
        pass

    p = (om.get("Poster") or "").strip()
    if p and p.lower() != "n/a" and not out.get("poster_url"):
        out["poster_url"] = p

    if not out.get("actors"):
        out["actors"] = (om.get("Actors") or "").strip()

    if not out.get("director"):
        out["director"] = (om.get("Director") or "").strip()

    if not out.get("runtime"):
        out["runtime"] = (om.get("Runtime") or "").strip()

    if not out.get("description"):
        out["description"] = (om.get("Plot") or "").strip()

    if not out.get("year"):
        out["year"] = (om.get("Year") or "")[:4]

    if not out.get("genre"):
        out["genre"] = (om.get("Genre") or "").strip()

    if not out.get("title"):
        out["title"] = (om.get("Title") or clean).strip()

    imdb_id = (om.get("imdbID") or "").strip()
    if imdb_id:
        out["imdb_id"] = imdb_id

    om_type = (om.get("Type") or "").strip().lower()
    if om_type == "series" and not out.get("media_type"):
        out["media_type"] = "tv"
    elif om_type == "movie" and not out.get("media_type"):
        out["media_type"] = "movie"

    return out


def _blocking_fetch(clean, y_hint, raw_title, alt_title=""):
    """
    Returns dict:
      title, year, genre, description, poster_url,
      actors, director, runtime,
      imdb_id, imdb_rating, tmdb_rating, tmdb_id

    Search path now mirrors the legacy YoureWatching method:
      - search by cleaned title first
      - optional alt_title from short EPG description
      - Arabic dual-pass (ar-AE then no language)
      - non-Arabic dual-pass (sq-AL then no language)
      - popularity-first pick, preferring items with a backdrop
      - extract rating/year/poster/overview from the picked TMDB item
      - OMDb enrich remains as a secondary enhancement layer
    """
    clean = _check_alias(clean)
    alt_title = _check_alias(alt_title)
    kind_hint = _detect_kind_hint(raw_title)
    query_variants = _build_query_variants(clean, raw_title)
    if alt_title and alt_title not in query_variants:
        query_variants.append(alt_title)

    out = {
        "src": "",
        "media_type": kind_hint or "",
        "title": clean,
        "year": y_hint or "",
        "genre": "",
        "description": "",
        "poster_url": "",
        "actors": "",
        "director": "",
        "runtime": "",
        "imdb_id": "",
        "imdb_rating": None,
        "tmdb_rating": None,
        "tmdb_id": "",
    }

    def _fill_from_omdb_only(om, src_name):
        if not isinstance(om, dict):
            return None
        out["src"] = src_name
        out["title"] = (om.get("Title") or clean).strip()
        out["year"] = (om.get("Year") or "")[:4]
        out["genre"] = (om.get("Genre") or "").strip()
        out["description"] = (om.get("Plot") or "").strip()
        poster = (om.get("Poster") or "").strip()
        out["poster_url"] = "" if poster.lower() == "n/a" else poster
        out["actors"] = (om.get("Actors") or "").strip()
        out["director"] = (om.get("Director") or "").strip()
        out["runtime"] = (om.get("Runtime") or "").strip()
        out["imdb_id"] = (om.get("imdbID") or "").strip()
        out["media_type"] = "tv" if (om.get("Type") or "").strip().lower() == "series" else "movie"
        try:
            ir = (om.get("imdbRating") or "").strip()
            if ir and ir.lower() != "n/a":
                out["imdb_rating"] = float(ir)
        except Exception:
            pass
        return out

    if not TMDB_API_KEY or TMDB_API_KEY.startswith("PUT_"):
        om = _omdb_fetch_best(query_variants, y_hint, kind_hint)
        return _fill_from_omdb_only(om, "omdb_only")

    search_title = (clean or "").strip()
    tmdb_title = search_title
    is_arabic = _is_arabic_text(raw_title or search_title or alt_title)
    passes = _build_legacy_tmdb_passes(search_title, alt_title)

    results = []
    used_lang = ""
    for query, lang in passes:
        try:
            batch = _tmdb_search_multi(query, lang)
        except Exception:
            batch = []
        if batch:
            results = batch
            used_lang = lang
            _log('TMDB HIT "%s" lang=%s count=%d' % (query, (lang or "default"), len(batch)))
            break

    if not results and not is_arabic:
        query_to_translate = (alt_title or search_title or raw_title or "").strip()
        groq_title = _groq_deduce_title(query_to_translate)
        if groq_title:
            try:
                batch = _tmdb_search_multi(groq_title, "")
            except Exception:
                batch = []
            if batch:
                results = batch
                used_lang = ""
                tmdb_title = groq_title
                _log('TMDB GROQ RETRY "%s"' % groq_title)

        if not results:
            translated_title = _translate_text_via_mymemory(query_to_translate, "sq|en")
            if translated_title:
                try:
                    batch = _tmdb_search_multi(translated_title, "")
                except Exception:
                    batch = []
                if batch:
                    results = batch
                    used_lang = ""
                    tmdb_title = translated_title
                    _log('TMDB MM RETRY "%s"' % translated_title)

    if not results:
        om = _omdb_fetch_best(query_variants, y_hint, kind_hint)
        return _fill_from_omdb_only(om, "omdb_title_fallback")

    best, extracted_title = _pick_legacy_tmdb_result(search_title, results, is_arabic)
    if not best:
        om = _omdb_fetch_best(query_variants, y_hint, kind_hint)
        return _fill_from_omdb_only(om, "omdb_reject_fallback")

    media_type = _tmdb_media_type(best) or (kind_hint or "movie")
    out["media_type"] = media_type

    tmdb_id = best.get("id")
    if tmdb_id:
        out["tmdb_id"] = str(tmdb_id)

    if extracted_title:
        tmdb_title = extracted_title

    out["src"] = "tmdb_legacy_multi:%s:%s" % (media_type, used_lang or "default")
    out["title"] = tmdb_title or search_title or raw_title or clean

    try:
        vote = best.get("vote_average")
        if vote is not None and float(vote) > 0:
            out["tmdb_rating"] = float(vote)
    except Exception:
        out["tmdb_rating"] = None

    out["year"] = _tmdb_result_year(best) or (y_hint or "")
    out["poster_url"] = _tmdb_poster_url(best.get("poster_path"), "w342")

    preferred_detail_lang = "ar" if is_arabic else (used_lang or "en")
    if preferred_detail_lang == "ar-AE":
        preferred_detail_lang = "ar"
    detail_candidates = []
    for lang in (preferred_detail_lang, "en"):
        if lang and lang not in detail_candidates:
            detail_candidates.append(lang)

    det = None
    det_lang = detail_candidates[0] if detail_candidates else "en"
    for lang in detail_candidates:
        if not tmdb_id:
            break
        try:
            cand = _tmdb_details(tmdb_id, lang, media_type)
        except Exception:
            cand = None
        if isinstance(cand, dict):
            if det is None:
                det = cand
                det_lang = lang
            if (cand.get("overview") or "").strip():
                det = cand
                det_lang = lang
                break

    # overview / description (legacy behavior: Arabic details first, else MyMemory from English)
    desc = ""
    if is_arabic:
        ar_det = det if (isinstance(det, dict) and det_lang == "ar") else None
        if ar_det is None and tmdb_id:
            try:
                ar_det = _tmdb_details(tmdb_id, "ar", media_type)
            except Exception:
                ar_det = None
        if isinstance(ar_det, dict):
            desc = (ar_det.get("overview") or "").strip()
        if not desc:
            eng_overview = ""
            try:
                eng_det = _tmdb_details(tmdb_id, "en", media_type) if tmdb_id else None
            except Exception:
                eng_det = None
            if isinstance(eng_det, dict):
                eng_overview = (eng_det.get("overview") or "").strip()
            if not eng_overview:
                eng_overview = (best.get("overview") or "").strip()
            if eng_overview:
                desc = _translate_text_via_mymemory(eng_overview[:300], "en|ar") or eng_overview
    else:
        if isinstance(det, dict):
            desc = (det.get("overview") or "").strip()
        if not desc:
            desc = (best.get("overview") or "").strip()
    out["description"] = desc

    # genres: ids first, then detailed names if available
    genre_map = _GenreMap.ensure(media_type, det_lang or preferred_detail_lang or "en")
    try:
        gids = best.get("genre_ids") or []
        gnames = []
        for gid in gids:
            try:
                name = genre_map.get(int(gid))
                if name:
                    gnames.append(name)
            except Exception:
                pass
        if gnames:
            out["genre"] = ", ".join(gnames)
    except Exception:
        pass

    if isinstance(det, dict):
        try:
            gl = det.get("genres")
            if isinstance(gl, list) and gl:
                names = []
                for g in gl:
                    nm = g.get("name")
                    if nm:
                        names.append(str(nm))
                if names:
                    out["genre"] = ", ".join(names)
        except Exception:
            pass

        try:
            if media_type == "movie":
                rt = det.get("runtime")
                if isinstance(rt, int) and rt > 0:
                    out["runtime"] = rt
            else:
                rts = det.get("episode_run_time") or []
                for val in rts:
                    try:
                        ival = int(val)
                        if ival > 0:
                            out["runtime"] = ival
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        try:
            ex = det.get("external_ids") or {}
            imdb_id = (ex.get("imdb_id") or "").strip()
            if imdb_id:
                out["imdb_id"] = imdb_id
        except Exception:
            pass

        try:
            credits = det.get("credits") or {}
            cast = credits.get("cast") or []
            crew = credits.get("crew") or []
            cast_names = []
            for c in cast:
                nm = c.get("name")
                if nm:
                    cast_names.append(nm)
            out["actors"] = _join_names(cast_names, limit=12)

            dir_names = []
            for c in crew:
                try:
                    job = (c.get("job") or "").lower()
                    if job in ("director", "series director"):
                        nm = c.get("name")
                        if nm:
                            dir_names.append(nm)
                except Exception:
                    pass
            if not dir_names and media_type == "tv":
                try:
                    for c in (det.get("created_by") or []):
                        nm = c.get("name")
                        if nm:
                            dir_names.append(nm)
                except Exception:
                    pass
            out["director"] = _join_names(dir_names, limit=3)
        except Exception:
            pass

    imdb_id = (out.get("imdb_id") or "").strip()
    om = None
    if imdb_id:
        om = _omdb_fetch_by_imdbid(imdb_id)
    else:
        om_queries = []
        seen_q = set()
        for val in query_variants + [tmdb_title, best.get("original_title"), best.get("original_name")]:
            vv = (val or "").strip()
            if not vv:
                continue
            kk = vv.lower()
            if kk in seen_q:
                continue
            seen_q.add(kk)
            om_queries.append(vv)
        om = _omdb_fetch_best(om_queries[:MAX_QUERY_VARIANTS], y_hint, media_type)

    if isinstance(om, dict):
        out["src"] += "+omdb"
        _fill_from_omdb(out, om, clean)

    return out


# -----------------------------
# Converter
# -----------------------------
class furyMovieInfoOMDbEPG(Converter, object):
    """
    Usage in skin:
      <convert type="furyMovieInfoOMDbEPG">rating</convert>
      <convert type="furyMovieInfoOMDbEPG">year</convert>
      <convert type="furyMovieInfoOMDbEPG">genre</convert>
      <convert type="furyMovieInfoOMDbEPG">desc</convert>
      <convert type="furyMovieInfoOMDbEPG">actors</convert>
      <convert type="furyMovieInfoOMDbEPG">director</convert>
      <convert type="furyMovieInfoOMDbEPG">runtime</convert>
      <convert type="furyMovieInfoOMDbEPG">poster</convert>
      <convert type="furyMovieInfoOMDbEPG">imdburl</convert>
      <convert type="furyMovieInfoOMDbEPG">line</convert>
    """

    TITLE = 0
    RATING = 1
    YEAR = 2
    GENRE = 3
    DESC = 4
    RAW = 5
    LINE = 6

    POSTER_URL = 7
    ACTORS = 8
    DIRECTOR = 9
    RUNTIME = 10
    IMDB_ID = 11
    IMDB_URL = 12
    TMDB_ID = 13
    IMDB_RATING = 14
    TMDB_RATING = 15
    JSON = 16

    def __init__(self, type):
        Converter.__init__(self, type)
        t = (type or "").strip().lower()

        if t == "rating":
            self.type = self.RATING
        elif t == "year":
            self.type = self.YEAR
        elif t == "genre":
            self.type = self.GENRE
        elif t in ("desc", "description", "plot"):
            self.type = self.DESC
        elif t in ("raw", "debug"):
            self.type = self.RAW
        elif t in ("line", "short"):
            self.type = self.LINE

        # extended
        elif t in ("poster", "posterurl", "poster_url", "posterlink", "image", "cover"):
            self.type = self.POSTER_URL
        elif t in ("actors", "cast"):
            self.type = self.ACTORS
        elif t in ("director", "dir"):
            self.type = self.DIRECTOR
        elif t in ("runtime", "duration", "time"):
            self.type = self.RUNTIME
        elif t in ("imdbid", "imdb_id", "imdb"):
            self.type = self.IMDB_ID
        elif t in ("imdburl", "imdb_url", "imdb_link"):
            self.type = self.IMDB_URL
        elif t in ("tmdbid", "tmdb_id"):
            self.type = self.TMDB_ID
        elif t in ("imdbrating", "imdb_rating"):
            self.type = self.IMDB_RATING
        elif t in ("tmdbrating", "tmdb_rating"):
            self.type = self.TMDB_RATING
        elif t in ("json", "data"):
            self.type = self.JSON
        else:
            self.type = self.TITLE

        # timers (debounce + polling)
        self._poll_timer = None
        self._debounce_timer = None
        self._poll_key = ""
        self._pending = None  # (key, clean, y_hint, raw_title, alt_title)
        self._pending_key = ""

        if eTimer is not None:
            # polling timer
            try:
                self._poll_timer = eTimer()
                try:
                    self._poll_timer.timeout.connect(self._on_poll)
                except Exception:
                    self._poll_timer.callback.append(self._on_poll)
            except Exception:
                self._poll_timer = None

            # debounce timer
            try:
                self._debounce_timer = eTimer()
                try:
                    self._debounce_timer.timeout.connect(self._on_debounce)
                except Exception:
                    self._debounce_timer.callback.append(self._on_debounce)
            except Exception:
                self._debounce_timer = None

    # -------------------------
    # Event getter (supports different sources)
    # -------------------------
    def _get_event(self):
        try:
            ev = getattr(self.source, "event", None)
            if ev:
                return ev
        except Exception:
            pass
        # Some sources may expose getEvent / getCurrentEvent
        for name in ("getEvent", "getCurrentEvent", "getCurrentServiceEvent"):
            try:
                fn = getattr(self.source, name, None)
                if callable(fn):
                    ev = fn()
                    if ev:
                        return ev
            except Exception:
                continue
        return None

    def _current_event_title(self):
        ev = self._get_event()
        if not ev:
            return ""
        try:
            return (ev.getEventName() or "").strip()
        except Exception:
            return ""

    # -------------------------
    # Debounce scheduling
    # -------------------------
    def _schedule_fetch(self, key, clean, y_hint, raw_title, alt_title=""):
        if not key:
            return

        # If already ready in cache, no need to schedule
        item = _AsyncCache.get(key)
        if item and item.get("status") == "ready" and item.get("data"):
            return

        self._pending = (key, clean, y_hint, raw_title, alt_title)
        self._pending_key = key

        if self._debounce_timer:
            try:
                self._debounce_timer.stop()
            except Exception:
                pass
            try:
                self._debounce_timer.start(int(DEBOUNCE_SECONDS * 1000), True)
            except Exception:
                pass
        else:
            # fallback (thread)
            threading.Timer(DEBOUNCE_SECONDS, self._on_debounce).start()

    def _on_debounce(self):
        pend = self._pending
        if not pend:
            return
        key, clean, y_hint, raw_title, alt_title = pend

        # make sure still current pending key
        if key != self._pending_key:
            return

        if not _AsyncCache.try_mark_inflight(key):
            # already inflight/ready/backoff
            return

        def worker():
            try:
                data = _blocking_fetch(clean, y_hint, raw_title, alt_title)
                if data:
                    _AsyncCache.set_ready(key, data)
                    _log("OK %s -> %s (%s)" % (clean, data.get("title"), data.get("src")))
                else:
                    _AsyncCache.set_neg(key)
                    _log("NOT FOUND %s" % clean)
            except Exception:
                _AsyncCache.set_neg(key)
                _log("WORKER ERROR\n" + traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        self._start_polling(key)

    # -------------------------
    # Polling
    # -------------------------
    def _start_polling(self, key):
        if not self._poll_timer:
            return
        self._poll_key = key
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        try:
            self._poll_timer.start(int(POLL_MS), True)
        except Exception:
            pass

    def _stop_polling(self):
        if not self._poll_timer:
            return
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        self._poll_key = ""

    def _on_poll(self):
        # Trigger UI refresh; if still inflight, keep polling; if ready, stop.
        try:
            # Check if ready now, if yes stop
            key = self._poll_key
            if key:
                item = _AsyncCache.get(key)
                if item and item.get("status") == "ready" and item.get("data"):
                    self._stop_polling()
                else:
                    # continue polling
                    self._start_polling(key)
            self.changed((self.CHANGED_POLL,))
        except Exception:
            try:
                Converter.changed(self, (self.CHANGED_POLL,))
            except Exception:
                pass

    # -------------------------
    # Main output
    # -------------------------
    @cached
    def getText(self):
        ev = self._get_event()
        raw_title = self._current_event_title()

        if self.type == self.RAW:
            return _safe(raw_title, "(no event)")

        clean, y_hint = _clean_title(raw_title)
        clean = _check_alias(clean)
        alt_title = _event_alt_title(ev)
        if not clean:
            self._stop_polling()
            if self.type == self.LINE:
                return "—/10  |  —  |  غير محدد"
            if self.type == self.DESC:
                return _aifury_translate_desc_cached_or_async(_safe(_event_desc(ev), "لا يوجد وصف متاح."))
            if self.type in (self.POSTER_URL, self.ACTORS, self.DIRECTOR, self.RUNTIME, self.IMDB_ID, self.IMDB_URL, self.TMDB_ID, self.IMDB_RATING, self.TMDB_RATING):
                return ""
            if self.type == self.JSON:
                return "{}"
            return "—"

        key = _make_cache_key(clean, y_hint, raw_title)

        # Ensure a fetch is scheduled for this key
        self._schedule_fetch(key, clean, y_hint, raw_title, alt_title)

        item = _AsyncCache.get(key)
        data = item.get("data") if item and item.get("status") == "ready" else None

        if not data:
            # show instant fallback (title/epg desc) while fetching
            self._start_polling(key)

            if self.type == self.LINE:
                return "—/10  |  —  |  غير محدد"
            if self.type == self.TITLE:
                return _safe(raw_title, "—")
            if self.type == self.RATING:
                return "—/10"
            if self.type == self.YEAR:
                return "—"
            if self.type == self.GENRE:
                return "غير محدد"
            if self.type == self.DESC:
                return _aifury_translate_desc_cached_or_async(_safe(_event_desc(ev), "لا يوجد وصف متاح."))
            if self.type in (self.POSTER_URL, self.ACTORS, self.DIRECTOR, self.RUNTIME, self.IMDB_ID, self.IMDB_URL, self.TMDB_ID, self.IMDB_RATING, self.TMDB_RATING):
                return ""
            if self.type == self.JSON:
                return "{}"
            return "—"

        # ready
        self._stop_polling()

        # fields
        title = data.get("title") or raw_title or clean
        year = data.get("year") or y_hint or ""
        genre = data.get("genre") or ""
        desc = data.get("description") or ""
        poster_url = data.get("poster_url") or ""
        actors = data.get("actors") or ""
        director = data.get("director") or ""
        runtime = data.get("runtime")
        imdb_id = (data.get("imdb_id") or "").strip()
        imdb_rating = data.get("imdb_rating", None)
        tmdb_rating = data.get("tmdb_rating", None)
        tmdb_id = (data.get("tmdb_id") or "").strip()

        # rating preference aligned with the legacy plugin: TMDB first, then IMDb fallback
        best_rating = tmdb_rating if tmdb_rating is not None else imdb_rating

        if self.type == self.LINE:
            rtxt = _fmt_rating(best_rating)
            ytxt = _safe(year, "—")
            gtxt = _safe(genre, "غير محدد")
            return "%s  |  %s  |  %s" % (rtxt, ytxt, gtxt)

        if self.type == self.TITLE:
            return _safe(title, _safe(raw_title, "—"))
        if self.type == self.RATING:
            return _fmt_rating(best_rating)
        if self.type == self.IMDB_RATING:
            return _fmt_rating(imdb_rating)
        if self.type == self.TMDB_RATING:
            return _fmt_rating(tmdb_rating)
        if self.type == self.YEAR:
            return _safe(year, "—")
        if self.type == self.GENRE:
            return _safe(genre, "غير محدد")
        if self.type == self.DESC:
            return _aifury_translate_desc_cached_or_async(_safe(desc, _safe(_event_desc(ev), "لا يوجد وصف متاح.")))

        if self.type == self.POSTER_URL:
            return _safe(poster_url, "")
        if self.type == self.ACTORS:
            return _safe(actors, "")
        if self.type == self.DIRECTOR:
            return _safe(director, "")
        if self.type == self.RUNTIME:
            return _fmt_runtime(runtime)
        if self.type == self.IMDB_ID:
            return _safe(imdb_id, "")
        if self.type == self.IMDB_URL:
            return ("https://www.imdb.com/title/%s/" % imdb_id) if imdb_id else ""
        if self.type == self.TMDB_ID:
            return _safe(tmdb_id, "")
        if self.type == self.JSON:
            try:
                return json.dumps(data, ensure_ascii=False, sort_keys=True)
            except Exception:
                return "{}"

        return "—"

    text = property(getText)

    def changed(self, what):
        # Standard invalidation for cached properties
        Converter.changed(self, what)

        # When event changes (not polling), schedule fetch immediately
        try:
            if what and what[0] == self.CHANGED_POLL:
                return
        except Exception:
            pass

        raw_title = self._current_event_title()
        clean, y_hint = _clean_title(raw_title)
        clean = _check_alias(clean)
        alt_title = _event_alt_title(self._get_event())
        if not clean:
            self._pending = None
            self._pending_key = ""
            self._stop_polling()
            return

        key = _make_cache_key(clean, y_hint, raw_title)
        self._schedule_fetch(key, clean, y_hint, raw_title, alt_title)
