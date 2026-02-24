# -*- coding: utf-8 -*-
from Components.Converter.Converter import Converter
from Components.Element import cached
import json, time, re, traceback, threading
from urllib.request import urlopen, Request
from urllib.parse import quote

# ------------------------------------------------------
# mod by islam salama skin Fury-FHD
# ------------------------------------------------------

# ======================================================
# ضع مفاتيحك هنا
# ======================================================
TMDB_API_KEY = "a73256be6d80f7b7d7448673a6ff24ee"
# ======================================================

try:
    from Components.config import config
    from Plugins.Extensions.AIFury.plugin import AIFuryController
except Exception:
    config = None
    AIFuryController = None

import os
try:
    from enigma import eTimer
except Exception:
    eTimer = None


_DESC_CACHE_FILE = "desc_cache.json"

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
        # Prefer HDD then USB (as requested), then fall back to plugin cachepath, then /tmp.
        candidates = [
            "/media/hdd/AIFury",
            "/media/usb/AIFury",
        ]
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

def _aifury_translate_desc_cached_or_async(text: str):
    """Non-blocking description translation with disk persistence."""
    if not text:
        return text
    norm = str(text).strip()
    if not norm:
        return text

    lang = _aifury_get_lang()
    key = (lang + "|" + norm).lower()

    # Disk cache first (non-blocking load)
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

    # Persist only if looks like real translation (changed)
    try:
        if isinstance(out, str):
            o = out.strip()
            if o and o != norm:
                _DescDiskCache.set(key, o)
                return o
    except Exception:
        pass

    # Fallback: return whatever we got, or the original
    return out if isinstance(out, str) and out else text

def _event_desc(source):
    """Fallback EPG description from current event (extended -> short)."""
    try:
        ev = getattr(source, "event", None)
        if not ev:
            return ""
        d = (ev.getExtendedDescription() or "").strip()
        if d:
            return d
        d = (ev.getShortDescription() or "").strip()
        return d
    except Exception:
        return ""


# ======================================================
# أداء (سريع)
# ======================================================
TIMEOUT = 4
CACHE_SECONDS = 7200            # كاش ساعتين
DEBOUNCE_SECONDS = 0.4          # أسرع من قبل
DEBUG_LOG = "/tmp/movieinfo_superfast.log"

def _log(msg):
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except:
        pass

def _safe(v, fallback="—"):
    if v is None:
        return fallback
    s = str(v).strip()
    return s if s else fallback

def _fmt_rating(r):
    if isinstance(r, (int, float)):
        r = max(0.0, min(10.0, float(r)))
        return f"{r:.1f}/10"
    return "—/10"

def _clean_title(title: str):
    """Return (clean_title, year_hint)"""
    t = (title or "").strip()
    if not t:
        return "", ""
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

    junk = [
        r"\bHD\b", r"\bFHD\b", r"\bUHD\b", r"\b4K\b",
        r"\b1080p\b", r"\b720p\b", r"\bH\.264\b", r"\bHEVC\b",
        r"\bWEB\b", r"\bDL\b", r"\bBluRay\b",
        r"^Movie\s*:\s*", r"^Film\s*:\s*", r"^فيلم\s*:\s*",
        r"\[.*?\]",
    ]
    for pat in junk:
        t = re.sub(pat, "", t, flags=re.IGNORECASE).strip()
    t = t.strip(" -_|:·•")
    return t, y

def _detect_lang_hint(text: str) -> str:
    # اختيار لغة عرض TMDB (للعربي/الروسي) بدون تعقيد
    if not text:
        return "en"
    for ch in text:
        o = ord(ch)
        if (0x0600 <= o <= 0x06FF) or (0x0750 <= o <= 0x077F) or (0x08A0 <= o <= 0x08FF):
            return "ar"
        if 0x0400 <= o <= 0x04FF:
            return "ru"
    return "en"

def _http_json(url: str):
    req = Request(url, headers={"User-Agent": "Enigma2-MovieInfo/1.0"})
    with urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw)

class _GenreMap:
    lock = threading.Lock()
    maps = {}  # lang -> (ts, {id: name})

    @classmethod
    def get(cls, lang: str):
        now = int(time.time())
        with cls.lock:
            item = cls.maps.get(lang)
            if item:
                ts, mp = item
                if (now - ts) < CACHE_SECONDS:
                    return mp
        return None

    @classmethod
    def set(cls, lang: str, mp: dict):
        now = int(time.time())
        with cls.lock:
            cls.maps[lang] = (now, mp)

    @classmethod
    def ensure(cls, lang: str):
        mp = cls.get(lang)
        if mp is not None:
            return mp
        # fetch once
        try:
            url = f"https://api.themoviedb.org/3/genre/movie/list?api_key={TMDB_API_KEY}&language={lang}"
            data = _http_json(url)
            genres = data.get("genres") or []
            mp = {}
            for g in genres:
                gid = g.get("id")
                name = g.get("name")
                if gid and name:
                    mp[int(gid)] = str(name)
            cls.set(lang, mp)
            return mp
        except Exception:
            return {}


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
    def should_start_fetch(cls, key):
        """Return True if a new background fetch is allowed for this key."""
        now = time.time()
        with cls.lock:
            item = cls.store.get(key)
            if not item:
                cls.store[key] = {"ts": int(now), "status": "inflight", "data": None, "attempts": 0, "next_try": 0.0}
                return True

            st = item.get("status")
            if st == "inflight":
                return False

            # If previous attempts failed, obey backoff window
            nxt = float(item.get("next_try", 0.0) or 0.0)
            if now < nxt:
                return False

            # start another attempt
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

            # exponential-ish backoff capped at 60s to avoid hammering TMDB
            # 1:2s, 2:5s, 3:10s, 4:20s, 5+:30s..60s
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


class furyMovieInfoOMDbEPG(Converter, object):
    """Super-fast: TMDB search (any language) + cached genre map. No OMDb calls."""

    TITLE = 0
    RATING = 1
    YEAR = 2
    GENRE = 3
    DESC = 4
    RAW = 5
    LINE = 6

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
        else:
            self.type = self.TITLE

        self._last_key = ""
        self._last_key_ts = 0.0

        # Non-blocking UI refresh while background search runs
        self._poll_timer = None
        self._poll_key = ""
        if eTimer is not None:
            try:
                self._poll_timer = eTimer()
                try:
                    self._poll_timer_conn = self._poll_timer.timeout.connect(self._on_poll)
                except Exception:
                    self._poll_timer.callback.append(self._on_poll)
            except Exception:
                self._poll_timer = None

    def _current_event_title(self):
        try:
            ev = getattr(self.source, "event", None)
            if not ev:
                return ""
            return (ev.getEventName() or "").strip()
        except:
            return ""

    def _blocking_fetch(self, clean: str, y_hint: str, raw_title: str):
        if not TMDB_API_KEY or TMDB_API_KEY.startswith("PUT_"):
            return None

        lang = _detect_lang_hint(raw_title or clean)
        genre_map = _GenreMap.ensure(lang)

        # 1 call only (fast)
        q = quote(clean)
        url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={q}&language={lang}"
        if y_hint:
            url += f"&year={quote(y_hint)}"

        data = _http_json(url)
        results = data.get("results") or []
        if not results:
            # fallback to english search if localized search fails
            if lang != "en":
                url2 = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={q}&language=en"
                if y_hint:
                    url2 += f"&year={quote(y_hint)}"
                data2 = _http_json(url2)
                results = data2.get("results") or []
                if not results:
                    return None
                # also switch genre map to en
                genre_map = _GenreMap.ensure("en")
            else:
                return None

        r0 = results[0]
        title = (r0.get("title") or raw_title or clean).strip()
        overview = (r0.get("overview") or "").strip()
        release = (r0.get("release_date") or "").strip()
        year = release[:4] if len(release) >= 4 else (y_hint or "")

        # TMDB vote_average is /10
        vote = r0.get("vote_average", None)
        try:
            rating = float(vote)
        except:
            rating = None

        gids = r0.get("genre_ids") or []
        gnames = []
        for gid in gids:
            try:
                name = genre_map.get(int(gid))
                if name:
                    gnames.append(name)
            except:
                pass
        genre = ", ".join(gnames) if gnames else ""

        return {
            "src": f"tmdb_search:{lang}",
            "title": title,
            "rating": rating,
            "year": year,
            "genre": genre,
            "description": overview,
        }

    def _maybe_start_fetch(self, key: str, clean: str, y_hint: str, raw_title: str):
        now = time.time()
        if key != self._last_key:
            self._last_key = key
            self._last_key_ts = now
            return
        if (now - self._last_key_ts) < DEBOUNCE_SECONDS:
            return

        if not _AsyncCache.should_start_fetch(key):
            return

        def worker():
            try:
                data = self._blocking_fetch(clean, y_hint, raw_title)
                if data:
                    _AsyncCache.set_ready(key, data)
                    _log(f"OK {data.get('src')}: {clean} -> {data.get('title')}")
                else:
                    _AsyncCache.set_neg(key)
                    _log(f"NOT FOUND: {clean} ({y_hint})")
            except Exception:
                _AsyncCache.set_neg(key)
                _log("WORKER ERROR\n" + traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()


    def _start_polling(self, key):
        """Refresh converter output periodically until TMDB result becomes ready."""
        if not self._poll_timer:
            return
        if key != self._poll_key:
            self._poll_key = key
        try:
            # poll every 1s while searching; cheap and avoids UI freezing
            self._poll_timer.start(1000, True)
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
        # trigger skin refresh
        try:
            self.changed((self.CHANGED_POLL,))
        except Exception:
            try:
                Converter.changed(self, (self.CHANGED_POLL,))
            except Exception:
                pass

    
    @cached
    def getText(self):
        raw_title = self._current_event_title()

        if self.type == self.RAW:
            return _safe(raw_title, "(no event)")

        clean, y_hint = _clean_title(raw_title)
        if not clean:
            self._stop_polling()
            if self.type == self.LINE:
                return "—/10  |  —  |  غير محدد"
            if self.type == self.DESC:
                return _aifury_translate_desc_cached_or_async(_safe(_event_desc(self.source), "لا يوجد وصف متاح."))
            return "—"

        key = (clean + "|" + (y_hint or "")).lower()

        item = _AsyncCache.get(key)
        if not item:
            self._maybe_start_fetch(key, clean, y_hint, raw_title)

        item = _AsyncCache.get(key)
        data = item.get("data") if item and item.get("status") == "ready" else None

        if not data:
            # Keep searching in background with backoff; refresh UI automatically without blocking navigation.
            try:
                self._maybe_start_fetch(key, clean, y_hint, raw_title)
            except Exception:
                pass
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
            return _aifury_translate_desc_cached_or_async(_safe(_event_desc(self.source), "لا يوجد وصف متاح."))

        # Data is ready: stop polling and return quickly.
        self._stop_polling()

        if self.type == self.LINE:
            rtxt = _fmt_rating(data.get("rating", None))
            ytxt = _safe(data.get("year"), "—")
            gtxt = _safe(data.get("genre"), "غير محدد")
            return f"{rtxt}  |  {ytxt}  |  {gtxt}"

        if self.type == self.TITLE:
            return _safe(data.get("title"), _safe(raw_title, "—"))
        if self.type == self.RATING:
            return _fmt_rating(data.get("rating", None))
        if self.type == self.YEAR:
            return _safe(data.get("year"), "—")
        if self.type == self.GENRE:
            return _safe(data.get("genre"), "غير محدد")
        return _aifury_translate_desc_cached_or_async(_safe(data.get("description"), _safe(_event_desc(self.source), "لا يوجد وصف متاح.")))

    text = property(getText)




    def changed(self, what):
        Converter.changed(self, what)
