# -*- coding: utf-8 -*-
# AIFury plugin v2.0  24/12/2025
# patch: INFO key opens info screen; Blue shows stats
# mod by islam salama (( skin fury )) v:2.1
from Components.Pixmap import Pixmap
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Components.Label import Label
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.MenuList import MenuList
from Components.config import (
    config,
    ConfigSubsection,
    ConfigYesNo,
    ConfigSelection,
    ConfigText,
    ConfigNothing,
    getConfigListEntry,
    configfile,
)
import os
import io
import json
import threading
import time
import traceback
import random

# ---------- embedded google_translate_api.py (merged into plugin.py) ----------
#
# It was programmed by Islam Salama for Skin Fury  1/12/2025.
# Updated to align with AIFury plugin async/cached translation flow:
# - Optional file/callback logging (so plugin can log in same cache path).
# - Configurable timeout; never raises to caller (returns "" on failure).
#
# NOTE: Backwards-compatible: translate_text(text, target_lang="ar") still works.

import sys
import json
import time

PY3 = sys.version_info[0] >= 3

try:
    # Python 2
    from urllib import urlencode
    import urllib2 as request_mod
except ImportError:
    # Python 3
    from urllib.parse import urlencode
    import urllib.request as request_mod


TARGET_LANG = "ar"
DEFAULT_TIMEOUT = 8  # seconds

version = "2.1"

# Optional throttling to reduce bans/throttling when translating many EPG events
_MIN_INTERVAL_MS = 0
_LAST_REQ_TS = 0.0
try:
    import threading as _threading
    _RATE_LOCK = _threading.Lock()
except Exception:
    _RATE_LOCK = None


def set_min_interval_ms(ms):
    """Set a minimum delay between translation requests (module-global)."""
    global _MIN_INTERVAL_MS
    try:
        _MIN_INTERVAL_MS = int(ms) if ms is not None else 0
    except Exception:
        _MIN_INTERVAL_MS = 0


def _rate_limit(min_interval_ms=None):
    """Best-effort rate limiter."""
    global _LAST_REQ_TS
    try:
        ms = _MIN_INTERVAL_MS if min_interval_ms is None else int(min_interval_ms)
    except Exception:
        ms = _MIN_INTERVAL_MS
    if not ms or ms <= 0:
        return
    delay = float(ms) / 1000.0
    try:
        if _RATE_LOCK is not None:
            with _RATE_LOCK:
                now = time.time()
                wait = (_LAST_REQ_TS + delay) - now
                if wait > 0:
                    time.sleep(wait)
                _LAST_REQ_TS = time.time()
            return
    except Exception:
        pass
    # fallback without lock
    try:
        now = time.time()
        wait = (_LAST_REQ_TS + delay) - now
        if wait > 0:
            time.sleep(wait)
        _LAST_REQ_TS = time.time()
    except Exception:
        pass


_DEFAULT_LOG_PATH = None


def set_default_log_path(path):
    """Set a default log file path for this module (optional)."""
    global _DEFAULT_LOG_PATH
    _DEFAULT_LOG_PATH = path


def _now_iso():
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "time?"


def _log(msg, log_path=None, log_fn=None):
    """Lightweight logging that never raises."""
    try:
        if log_fn:
            try:
                log_fn(msg)
                return
            except Exception:
                pass

        path = log_path or _DEFAULT_LOG_PATH
        if not path:
            return

        # Append one line per event
        try:
            f = open(path, "a")
            try:
                f.write("[%s] %s\n" % (_now_iso(), msg))
            finally:
                f.close()
        except Exception:
            # Never let logging break translation
            return
    except Exception:
        return


def _to_unicode(text):
    if text is None:
        return u""
    try:
        if PY3:
            if isinstance(text, str):
                return text
            else:
                return text.decode("utf-8", "ignore")
        else:
            if isinstance(text, unicode):  # noqa: F821
                return text
            else:
                return text.decode("utf-8", "ignore")
    except Exception:
        try:
            return str(text)
        except Exception:
            return u""


def is_mostly_arabic(text):
    """
    يحدد إذا كان النص بالكامل تقريباً عربي.
    لو نعم، مش هنترجمه وهنرجّعه زي ما هو.
    """
    t = _to_unicode(text)
    if not t:
        return False

    total_letters = 0
    arabic_letters = 0

    for ch in t:
        code = ord(ch)
        # حروف لاتينية أو عربية
        if (0x0041 <= code <= 0x005A) or (0x0061 <= code <= 0x007A) or (0x0600 <= code <= 0x06FF):
            total_letters += 1
            if 0x0600 <= code <= 0x06FF:
                arabic_letters += 1

    if total_letters == 0:
        return False

    try:
        ratio = float(arabic_letters) / float(total_letters)
    except Exception:
        return False

    # لو 60% أو أكتر من الحروف عربية → اعتبره عربي
    return ratio >= 0.6


def is_arabic_word(word):
    """
    يحدد إذا كانت الكلمة عربية في الغالب (للاستخدام في التصحيح التلقائي).
    """
    w = _to_unicode(word)
    if not w:
        return False

    letters = [ch for ch in w if ch.strip()]
    if not letters:
        return False

    arabic = 0
    for ch in letters:
        code = ord(ch)
        if 0x0600 <= code <= 0x06FF:
            arabic += 1

    try:
        ratio = float(arabic) / float(len(letters))
    except Exception:
        return False

    return ratio >= 0.6


def levenshtein_distance(a, b, max_distance=2):
    """
    حساب مسافة ليفنشتاين بين كلمتين مع حد أقصى للتسريع.
    """
    a = _to_unicode(a)
    b = _to_unicode(b)

    if a == b:
        return 0

    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    if len(a) == 0:
        return len(b)
    if len(b) == 0:
        return len(a)

    # خلي a هي الأقصر
    if len(a) > len(b):
        a, b = b, a

    previous_row = list(range(len(b) + 1))

    for i, ca in enumerate(a, 1):
        current_row = [i]
        min_in_row = i
        for j, cb in enumerate(b, 1):
            insert_cost = current_row[j - 1] + 1
            delete_cost = previous_row[j] + 1
            replace_cost = previous_row[j - 1] + (0 if ca == cb else 1)
            c = min(insert_cost, delete_cost, replace_cost)
            current_row.append(c)
            if c < min_in_row:
                min_in_row = c
        # لو السطر كله بقى أكبر من max_distance نقدر نوقف بدري
        if min_in_row > max_distance:
            return max_distance + 1
        previous_row = current_row

    return previous_row[-1]


def post_process_arabic_text(original, translated):
    """
    طبقة تصحيح تلقائي بعد الترجمة:
    - لو الكلمة الأصلية عربية، والنتيجة عربية وقريبة منها (خطأ حرف واحد مثلاً)،
      نرجّع الكلمة الأصلية بدل ما نسيب غلطة جوجل.
    - تنظيف مسافات زائدة.
    """
    o = _to_unicode(original)
    t = _to_unicode(translated)

    # إزالة المسافات المتكررة
    while u"  " in t:
        t = t.replace(u"  ", u" ")

    o_words = o.split()
    t_words = t.split()

    if not o_words or not t_words:
        return t.strip()

    n = min(len(o_words), len(t_words))
    new_t_words = list(t_words)

    for i in range(n):
        ow = o_words[i]
        tw = t_words[i]

        # لو الاتنين كلمات عربية
        if is_arabic_word(ow) and is_arabic_word(tw):
            # وطول الكلمة الأصلي 3 حروف أو أكتر
            # ومسافة ليفنشتاين صغيرة (خطأ حرف واحد)
            if len(ow) >= 3 and levenshtein_distance(ow, tw, max_distance=1) <= 1:
                # استبدل الترجمة بالكلمة الأصلية (تصحيح تلقائي)
                new_t_words[i] = ow

    return u" ".join(new_t_words).strip()



def translate_text(text, target_lang=TARGET_LANG, timeout=DEFAULT_TIMEOUT, log_path=None, log_fn=None,
                   retries=0, min_interval_ms=None, backoff_base_s=0.35, backoff_jitter_s=0.2):
    """
    Multi-language Google Translate helper (unofficial endpoint).

    Behavior:
    - If target_lang is Arabic (starts with "ar") and the input is mostly Arabic, return the original text.
    - Otherwise, call translate.googleapis.com and return the translated text.
    - Apply post_process_arabic_text only when translating TO Arabic.
    - Never raises to caller; returns "" on failure (plugin will fallback to original).
    """
    text = _to_unicode(text)
    if not text:
        return u""

    try:
        _tl = (target_lang or u"").lower()
    except Exception:
        _tl = u""

    # Only skip Arabic input when the target is Arabic
    if _tl.startswith("ar") and is_mostly_arabic(text):
        return text

    base_url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": target_lang,
        "dt": "t",
        "q": text.encode("utf-8") if not PY3 else text,
    }

    t0 = None
    try:
        max_attempts = max(0, int(retries)) + 1
    except Exception:
        max_attempts = 1

    for attempt in range(max_attempts):
        try:
            t0 = time.time()
            _rate_limit(min_interval_ms)

            query_string = urlencode(params)
            if PY3 and isinstance(query_string, bytes):
                query_string = query_string.decode("utf-8")

            url = base_url + "?" + query_string

            req = request_mod.Request(url)
            try:
                req.add_header("User-Agent", "Mozilla/5.0 (AIFury)")
            except Exception:
                pass

            resp = request_mod.urlopen(req, timeout=timeout)
            try:
                raw = resp.read()
            finally:
                try:
                    resp.close()
                except Exception:
                    pass

            if PY3 and isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            data = json.loads(raw)

            translated_chunks = []
            if isinstance(data, list) and data:
                for item in data[0]:
                    if item and isinstance(item, list) and item[0]:
                        translated_chunks.append(item[0])

            translated_text = u"".join(translated_chunks).strip()
            if not translated_text:
                raise ValueError("empty translation")

            # Apply Arabic post-processing only when translating TO Arabic
            if _tl.startswith("ar"):
                translated_text = post_process_arabic_text(text, translated_text)

            # Logging (optional)
            try:
                dt = time.time() - t0 if t0 else 0
                sample = (text[:80] + "...") if len(text) > 80 else text
                _log("gt ok (%.3fs) tl=%s text=%s" % (dt, target_lang, sample), log_path=log_path, log_fn=log_fn)
            except Exception:
                pass

            return translated_text

        except Exception as e:
            # Log and retry
            try:
                dt = time.time() - t0 if t0 else 0
                sample = (text[:80] + "...") if len(text) > 80 else text
                _log("gt error (attempt %d/%d, %.3fs): %s | tl=%s | text=%s" %
                     (attempt + 1, max_attempts, dt, e, target_lang, sample),
                     log_path=log_path, log_fn=log_fn)
            except Exception:
                pass

            if attempt + 1 < max_attempts:
                try:
                    base = float(backoff_base_s) * (2 ** attempt)
                    jitter = float(backoff_jitter_s) if backoff_jitter_s else 0.0
                    time.sleep(base + (jitter * 0.5))
                except Exception:
                    pass
            continue

    return u""
# ---------- end embedded google_translate_api.py ----------

try:
    import queue as _queue
except Exception:
    try:
        import Queue as _queue
    except Exception:
        _queue = None

try:
    from enigma import eTimer, eEPGCache
except Exception:
    eTimer = None

try:
    from enigma import eServiceCenter, eServiceReference
except Exception:
    eServiceCenter = None
    eServiceReference = None

# ---------- ثابت لغات البلجن ----------

def _svc_name_from_ref(ref_str):
    """Return a best-effort service name for a ref string."""
    try:
        if eServiceCenter is None or eServiceReference is None:
            return ""
        sref = eServiceReference(ref_str)
        info = eServiceCenter.getInstance().info(sref)
        if info:
            return (info.getName(sref) or "").strip()
    except Exception:
        pass
    return ""


def _iter_bouquets(tv=True):
    """Yield bouquet directory refs from bouquets.tv (or bouquets.radio)."""
    try:
        if eServiceCenter is None or eServiceReference is None:
            return
        root = '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % ("bouquets.tv" if tv else "bouquets.radio")
        lst = eServiceCenter.getInstance().list(eServiceReference(root))
        if not lst:
            return
        while True:
            s = lst.getNext()
            if not s.valid():
                break
            yield s
    except Exception:
        return


def _find_favourites_bouquet_ref(tv=True):
    """
    Try to find the user's Favourites/Favorites bouquet.
    Fallback to the common userbouquet.favourites.(tv|radio) reference.
    """
    # Try real bouquet list first
    try:
        if eServiceCenter is not None and eServiceReference is not None:
            for b in _iter_bouquets(tv=tv) or []:
                try:
                    ref_str = b.toString()
                except Exception:
                    ref_str = ""
                try:
                    info = eServiceCenter.getInstance().info(b)
                    name = (info.getName(b) if info else "") or ""
                except Exception:
                    name = ""
                blob = ("%s %s" % (ref_str, name)).lower()
                if ("favourites" in blob) or ("favorites" in blob) or ("favourite" in blob) or ("favorite" in blob) or ("المفض" in blob) or ("مفضل" in blob):
                    return ref_str
    except Exception:
        pass

    # Fallback
    fname = "userbouquet.favourites.%s" % ("tv" if tv else "radio")
    return '1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "%s" ORDER BY bouquet' % fname


def _get_bouquet_choices(tv=True):
    """
    Return a list of (bouquet_ref_str, display_name) from bouquets.tv / bouquets.radio.
    """
    out = []
    try:
        if eServiceCenter is None or eServiceReference is None:
            return out
        for b in _iter_bouquets(tv=tv) or []:
            try:
                ref_str = b.toString()
            except Exception:
                ref_str = ""
            if not ref_str:
                continue
            try:
                info = eServiceCenter.getInstance().info(b)
                name = (info.getName(b) if info else "") or ""
            except Exception:
                name = ""
            name = name.strip() or ref_str
            out.append((ref_str, name))
    except Exception:
        out = []
    # de-dup and sort
    seen = set()
    uniq = []
    for r, n in out:
        if r in seen:
            continue
        seen.add(r)
        uniq.append((r, n))
    try:
        uniq.sort(key=lambda x: (x[1] or "").lower())
    except Exception:
        pass
    if not uniq:
        try:
            fav = _find_favourites_bouquet_ref(tv=tv)
            uniq = [(fav, "Favourites")]
        except Exception:
            pass
    return uniq


def _bouquet_name_from_ref(bouquet_ref_str):
    try:
        if eServiceCenter is None or eServiceReference is None:
            return ""
        bref = eServiceReference(bouquet_ref_str)
        info = eServiceCenter.getInstance().info(bref)
        return (info.getName(bref) if info else "") or ""
    except Exception:
        return ""


def _list_services_in_bouquet(bouquet_ref_str):
    """Return a list of playable service ref strings inside a bouquet."""
    out = []
    try:
        if eServiceCenter is None or eServiceReference is None:
            return out
        sref = eServiceReference(bouquet_ref_str)
        lst = eServiceCenter.getInstance().list(sref)
        if not lst:
            return out
        while True:
            s = lst.getNext()
            if not s.valid():
                break
            try:
                flags = int(getattr(s, "flags", 0))
                # skip directories/markers
                if hasattr(eServiceReference, "isDirectory") and (flags & eServiceReference.isDirectory):
                    continue
                if hasattr(eServiceReference, "isMarker") and (flags & eServiceReference.isMarker):
                    continue
            except Exception:
                pass
            try:
                out.append(s.toString())
            except Exception:
                pass
    except Exception:
        pass

    # De-dup keep order
    try:
        seen = set()
        out2 = []
        for r in out:
            if r in seen:
                continue
            seen.add(r)
            out2.append(r)
        out = out2
    except Exception:
        pass
    return out



AIFury_LANG_CHOICES = [
    ("ar", "Arabic"),
    ("en", "English"),
    ("fr", "French"),
    ("de", "German"),
    ("nl-NL", "Nederlands"),
    ("it", "Italian"),
    ("es", "Spanish"),
    ("nl-BE","Belgium"),
    ("pt", "Portuguese (Português)"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian (Українська)"),
    ("ru", "Russian"),
    ("zh", "Chinese (中文)"),
    ("ja", "Japanese (日本語)"),
    ("ko", "Korean (한국어)"),
    ("hi", "Hindi (हिन्दी)"),
    ("sv", "Swedish (Svenska)"),
    ("no", "Norwegian (Norsk)"),
    ("el", "Greek (Ελληνικά)"),
    ("hr", "Croatian (Hrvatski)"),
    ("sr", "Serbian (Srpski/Српски)"),
    ("bs", "Bosnian (Bosanski)"),
    ("sq", "Albanian (Shqip)"),
    ("bg", "Bulgarian (Български)"),
    ("gsw", "Swiss German (Schwiizerdütsch)"),
    ("rm", "Romansh (Rumantsch)"),
    ("fi", "Finnish (Suomi)"),
    ("ro", "Romanian (Română)"),
    ("hu", "Hungarian (Magyar)"),
    ("pl", "Polish (Polski)"),
    ("cs", "Czech (Čeština)"),
    ("sk", "Slovak (Slovenčina)"),
    ("sl", "Slovenian (Slovenščina)"),
    ("es-MX", "Spanish (Mexico)"),
    ("pt-BR", "Portuguese (Brazil)"),
    ("id", "Indonesian (Bahasa Indonesia)"),
    ("ur", "Urdu (اردو)"),
    ("fa", "Persian/Farsi (فارسی)"),
]


def get_default_cache_path():
    """Return default cache file path based on available mount points."""
    candidates = [
        "/media/hdd",
        "/media/usb",
        "/media/usb0",
        "/media/usb1",
        "/media/mmc",
        "/media/mmc1",
        "/media/usb2",
        "/media/usb3",
        "/media/hdd1",
        "/media/hdd2",
    ]
    for base in candidates:
        if os.path.isdir(base):
            return os.path.join(base, "aifury_cache.json")
    return "/tmp/aifury_cache.json"


# ---------- configuration ----------

config.plugins.aifury = ConfigSubsection()
config.plugins.aifury.enabled = ConfigYesNo(default=True)
config.plugins.aifury.language = ConfigSelection(
    default="ar",
    choices=AIFury_LANG_CHOICES,
)

# EPG translation: allow separate languages for event title and descriptions
config.plugins.aifury.epg_title_lang = ConfigSelection(
    default="",
    choices=[("", "Disabled")] + AIFury_LANG_CHOICES,
)
# تظهر "no path" حتى يختار المستخدم مسارًا
config.plugins.aifury.cachepath = ConfigText(
    default="no path", fixed_size=False
)
# مسار مستقل لحفظ ترجمات (Translate Current Event) حتى لا تختفي عند التنقل بين القنوات
config.plugins.aifury.epgcachepath = ConfigText(
    default="no path", fixed_size=False
)

# maintenance
config.plugins.aifury.maint_reset_all = ConfigYesNo(default=False)
config.plugins.aifury.maint_reset_defaults = ConfigYesNo(default=False)
config.plugins.aifury.maint_clear_caches = ConfigYesNo(default=False)

# Restore translated EPG from disk cache automatically after channel zapping
config.plugins.aifury.auto_restore_epg = ConfigYesNo(default=True)

# Show completion notification when bouquet translation runs in background
config.plugins.aifury.bouquet_bg_notify_done = ConfigYesNo(default=True)

# Timeout (seconds) for 'translation finished' notification. 0 = until dismissed
config.plugins.aifury.done_notify_timeout = ConfigSelection(
    default="10",
    choices=[
        ("3", "3 seconds"),
        ("5", "5 seconds"),
        ("8", "8 seconds"),
        ("10", "10 seconds"),
        ("15", "15 seconds"),
        ("20", "20 seconds"),
        ("30", "30 seconds"),
        ("0", "Until dismissed"),
    ],
)



# Periodic keep-restore interval (seconds) for the currently playing service (0 = Off)
config.plugins.aifury.keep_restore_interval = ConfigSelection(
    default="90",
    choices=[
        ("0", "Off"),
        ("10", "10 seconds"),
        ("15", "15 seconds"),
        ("20", "20 seconds"),
        ("30", "30 seconds"),
        ("60", "60 seconds"),
        ("90", "90 seconds"),
        ("120", "2 minutes"),
        ("180", "3 minutes"),
        ("300", "5 minutes"),
        ("600", "10 minutes"),
    ],
)
# Auto translate EPG events in the background (no need to enter the plugin)
config.plugins.aifury.auto_translate_epg = ConfigYesNo(default=False)
# Minutes to translate ahead (EPG horizon)
config.plugins.aifury.auto_translate_horizon = ConfigSelection(
    default="20160",
    choices=[("180", "3 hours"), ("360", "6 hours"), ("720", "12 hours"), ("1440", "24 hours"), ("2880", "48 hours"), ("10080", "7 days")],
)
# Minimum minutes between background translations for the same service/language
config.plugins.aifury.auto_translate_min_gap = ConfigSelection(
    default="15",
    choices=[("5", "5"), ("10", "10"), ("15", "15"), ("30", "30"), ("60", "60")],
)
# Safety cap: max number of events per run (0 = no cap)
config.plugins.aifury.auto_translate_max_events = ConfigSelection(
    default="0",
    choices=[("0", "0 (no cap)"), ("500000", "50000"),  ("10000000", "10000000")],
)

config.plugins.aifury.last_bouquet_ref = ConfigText(default="")
config.plugins.aifury.enable_translate_current_event = ConfigYesNo(default=True)

# network / performance tuning
config.plugins.aifury.req_timeout = ConfigSelection(
    default="3",
    choices=[("2", "2"), ("3", "3"), ("5", "5"), ("8", "8")],
)
config.plugins.aifury.req_retries = ConfigSelection(
    default="1",
    choices=[("0", "0"), ("1", "1"), ("2", "2"), ("3", "3")],
)
config.plugins.aifury.min_interval_ms = ConfigSelection(
    default="250",
    choices=[("0", "0"), ("100", "100"), ("250", "250"), ("500", "500"), ("1000", "1000")],
)
config.plugins.aifury.workers = ConfigSelection(
    default="4",
    choices=[("0", "0 (disable pool)"), ("2", "2"), ("4", "4"), ("6", "6"), ("8", "8")],
)


# ---------- controller ----------

class AIFuryController(object):
    instance = None

    def __init__(self, session):
        print("[AIFury] Controller __init__ called, session=%s" % (session,))
        AIFuryController.instance = self
        self.session = session
        self.enabled = config.plugins.aifury.enabled.value

        # Determine cache path: treat "no path" كأنه فارغ
        cfg_raw = (config.plugins.aifury.cachepath.value or "").strip()
        if cfg_raw.lower() == "no path":
            cfg_path = ""
        else:
            cfg_path = cfg_raw

        if not cfg_path:
            # مسار داخلي افتراضي عندما لا يختار المستخدم مسارًا
            base = "/tmp"
            cfg_path = os.path.join(base, "AIFury", "aifury_cache.json")

        cache_dir = os.path.dirname(cfg_path)
        if cache_dir and not os.path.isdir(cache_dir):
            try:
                os.makedirs(cache_dir)
            except Exception as e:
                print("[AIFury] error creating cache dir %s: %s" % (cache_dir, e))

        self.cachepath = cfg_path

        # ----- Persistent EPG translations cache (for "Translate Current Event") -----
        cfg2_raw = (getattr(config.plugins.aifury, "epgcachepath", None).value if hasattr(config.plugins.aifury, "epgcachepath") else "") or ""
        cfg2_raw = (cfg2_raw or "").strip()
        if cfg2_raw.lower() == "no path":
            cfg2_path = ""
        else:
            cfg2_path = cfg2_raw

        if not cfg2_path:
            # افتراضي: نفس فولدر الكاش الأساسي لكن باسم ملف مختلف
            base_dir = os.path.dirname(self.cachepath) or "/tmp"
            cfg2_path = os.path.join(base_dir, "aifury_epg_on_cache")         
        epg_cache_dir = os.path.dirname(cfg2_path)
        if epg_cache_dir and not os.path.isdir(epg_cache_dir):
            try:
                os.makedirs(epg_cache_dir)
            except Exception as e:
                print("[AIFury] error creating epg cache dir %s: %s" % (epg_cache_dir, e))

        self.epg_cachepath = cfg2_path
        self.epg_cache = {}  # {service: {langkey: {"ts":..,"events":{eventKey:{t,s,l}}}}}
        self._epg_cache_lock = threading.Lock()
        self._epg_cache_dirty = 0
        self._epg_cache_save_interval = 3
        self._epg_cache_force_save = False  # force immediate disk flush after manual translate
        self.cache = {}
        self.logpath = os.path.join(os.path.dirname(self.cachepath) or "/tmp", "aifury.log")
        self._log_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = set()

        # rate limiting (seconds between outbound translation requests)
        self._rate_lock = threading.Lock()
        try:
            self._min_interval = float(int(config.plugins.aifury.min_interval_ms.value or "0")) / 1000.0
        except Exception:
            self._min_interval = 0.0
        self._last_req_ts = 0.0

        # background worker pool (reduces overhead vs. spawning threads per request)
        self._pool = None
        self._pool_size = 0
        self._ensure_pool()

        # UI callback queue (run callbacks on main thread when possible)
        self._ui_queue = []
        self._ui_timer = None
        if eTimer is not None:
            try:
                self._ui_timer = eTimer()
                self._ui_timer.callback.append(self._flush_ui_queue)
            except Exception:
                self._ui_timer = None

        # Auto-restore translated EPG (delayed after zapping)
        self._restore_timer = None
        self._restore_ref = None
        if eTimer is not None:
            try:
                self._restore_timer = eTimer()
                self._restore_timer.callback.append(self._restore_epg_from_cache_cb)
            except Exception:
                self._restore_timer = None

        # Periodic keep-restore for the currently playing service.
        # Some images refresh/overwrite EIT frequently; this timer re-imports translated events from epg_cache
        # so translations do not "revert" while staying on the same channel.
        self._keep_restore_timer = None
        self._keep_restore_interval_ms = self._get_keep_restore_interval_ms(default_sec=90)  # from config
        if eTimer is not None:
            try:
                self._keep_restore_timer = eTimer()
                self._keep_restore_timer.callback.append(self._keep_restore_epg_cb)
            except Exception:
                self._keep_restore_timer = None

        # Start keep-restore immediately (it will re-arm itself).
        try:
            if getattr(self, "_keep_restore_timer", None) is not None:
                interval_ms = int(getattr(self, "_keep_restore_interval_ms", 0) or 0)
                if interval_ms > 0 and hasattr(config.plugins.aifury, "auto_restore_epg") and config.plugins.aifury.auto_restore_epg.value:
                    self._timer_start_compat(self._keep_restore_timer, interval_ms)
        except Exception:
            pass

        # Bulk apply (restore/revert) EPG translations across multiple services without freezing GUI.
        self._bulk_epg_timer = None
        self._bulk_epg_queue = []
        self._bulk_epg_mode = None  # "restore" | "revert"
        self._bulk_epg_batch = 8
        self._bulk_title_lang = None
        self._bulk_descr_lang = None
        if eTimer is not None:
            try:
                self._bulk_epg_timer = eTimer()
                self._bulk_epg_timer.callback.append(self._bulk_epg_cb)
            except Exception:
                self._bulk_epg_timer = None

        
        # Auto-translate EPG in background (delayed after zapping)
        self._auto_tr_timer = None
        self._auto_tr_ref = None
        self._auto_tr_last = {}  # {(sref_str, langkey): ts}
        self._auto_tr_lock = threading.Lock()

        # Background auto-translate stats (since last restart)
        self._bg_stats = {"runs": 0, "translated": 0, "imported": 0, "last_ts": 0, "last_service": ""}
        self._bg_stats_lock = threading.Lock()

        if eTimer is not None:
            try:
                self._auto_tr_timer = eTimer()
                self._auto_tr_timer.callback.append(self._auto_translate_cb)
            except Exception:
                self._auto_tr_timer = None

        self._log("Controller initialized. cache=%s log=%s" % (self.cachepath, self.logpath))

        # cache tuning
        self._cache_dirty = 0
        self._cache_save_interval = 10
        self._cache_max_items = 10000000000000000000  # 0 = unlimited

        self._load_cache()
        self._load_epg_cache()
        self._patch_targets()

        # Attempt to restore translated EPG for the currently playing service
        try:
            self.schedule_epg_restore(self.session.nav.getCurrentlyPlayingServiceReference(), delay_ms=0)
            # Keep translations applied even if the image refreshes/overwrites EPG while staying on the same channel.
            try:
                if getattr(self, '_keep_restore_timer', None) is not None:
                    try:
                        self._keep_restore_interval_ms = self._get_keep_restore_interval_ms(default_sec=90)
                    except Exception:
                        pass
                    if int(getattr(self, '_keep_restore_interval_ms', 0) or 0) > 0:
                        self._timer_start_compat(self._keep_restore_timer, int(getattr(self, '_keep_restore_interval_ms', 0) or 0))
                    else:
                        try:
                            self._keep_restore_timer.stop()
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                self.schedule_auto_translate(self.session.nav.getCurrentlyPlayingServiceReference(), delay_ms=1200)
            except Exception:
                pass
        except Exception:
            pass

    # ---------- cache ----------

    def _load_cache(self):
        path = self.cachepath
        try:
            if os.path.exists(path):
                with io.open(path, "r", encoding="utf-8") as f:
                    data = f.read()
                if data.strip():
                    with self._cache_lock:
                        self.cache = json.loads(data)
                else:
                    with self._cache_lock:
                        self.cache = {}
                self._cache_dirty = 0
                print("[AIFury] cache loaded from %s (%d items)" % (path, len(self.cache)))
                try:
                    self._log("cache loaded: %s (%d items)" % (path, len(self.cache)))
                except Exception:
                    pass
            else:
                with self._cache_lock:
                    self.cache = {}
                self._cache_dirty = 0
                print("[AIFury] no cache file at %s, starting empty" % path)
                try:
                    self._log("no cache file, created: %s" % path)
                except Exception:
                    pass
                self._save_cache(force=True)
        except Exception as e:
            print("[AIFury] load_cache error: %s" % e)
            self.cache = {}
            self._cache_dirty = 0

    def _shrink_cache(self, max_items):
        try:
            if max_items <= 0 or len(self.cache) <= max_items:
                return
            with self._cache_lock:
                items = list(self.cache.items())
                self.cache = dict(items[-max_items:])
            print("[AIFury] cache trimmed to %d items" % len(self.cache))
        except Exception as e:
            print("[AIFury] cache shrink error: %s" % e)

    def _save_cache(self, force=False):
        if not force and getattr(self, "_cache_dirty", 0) <= 0:
            return
        path = self.cachepath
        try:
            d = os.path.dirname(path)
            if d and not os.path.exists(d):
                os.makedirs(d)
            with self._cache_lock:
                snapshot = dict(self.cache)
            with io.open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            self._cache_dirty = 0
            print("[AIFury] cache saved to %s (%d items)" % (path, len(self.cache)))
            try:
                self._log("cache saved: %s (%d items)" % (path, len(snapshot)))
            except Exception:
                pass
        except Exception as e:
            print("[AIFury] save_cache error: %s" % e)

    def clear_cache(self):
        with self._cache_lock:
            self.cache = {}
        self._cache_dirty = 0
        try:
            if os.path.exists(self.cachepath):
                os.remove(self.cachepath)
        except Exception as e:
            print("[AIFury] clear_cache error: %s" % e)
        print("[AIFury] cache cleared")
        try:
            self._log("cache cleared")
        except Exception:
            pass


    # ---------- persistent EPG translations cache ----------

    def _epg_lang_key(self, title_lang, descr_lang):
        return "%s|%s" % (title_lang or "", descr_lang or "")

    def _epg_event_key(self, event_id, begin, duration):
        try:
            return "%s|%s|%s" % (int(event_id or 0), int(begin or 0), int(duration or 0))
        except Exception:
            return "%s|%s|%s" % (event_id or 0, begin or 0, duration or 0)

    def _to_sref_str(self, service_ref):
        try:
            s = service_ref.toString() if hasattr(service_ref, "toString") else str(service_ref)
        except Exception:
            s = str(service_ref)
        try:
            return s.rstrip(":")
        except Exception:
            return s

    def _load_epg_cache(self):
        path = getattr(self, "epg_cachepath", "") or ""
        try:
            if not path:
                with self._epg_cache_lock:
                    self.epg_cache = {}
                self._epg_cache_dirty = 0
                return

            if os.path.exists(path):
                with io.open(path, "r", encoding="utf-8") as f:
                    data = f.read()
                if data.strip():
                    with self._epg_cache_lock:
                        self.epg_cache = json.loads(data)
                else:
                    with self._epg_cache_lock:
                        self.epg_cache = {}
                self._epg_cache_dirty = 0
                print("[AIFury] epg cache loaded from %s" % path)
            else:
                with self._epg_cache_lock:
                    self.epg_cache = {}
                self._epg_cache_dirty = 0
                # create empty file
                try:
                    self._save_epg_cache(force=True)
                except Exception:
                    pass
        except Exception as e:
            print("[AIFury] load_epg_cache error: %s" % e)
            with self._epg_cache_lock:
                self.epg_cache = {}
            self._epg_cache_dirty = 0

    def _save_epg_cache(self, force=False):
        if not force and getattr(self, "_epg_cache_dirty", 0) <= 0:
            return
        path = getattr(self, "epg_cachepath", "") or ""
        if not path:
            return
        try:
            d = os.path.dirname(path)
            if d and not os.path.exists(d):
                os.makedirs(d)
            with self._epg_cache_lock:
                snapshot = dict(self.epg_cache)
            with io.open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            self._epg_cache_dirty = 0
            print("[AIFury] epg cache saved to %s" % path)
        except Exception as e:
            print("[AIFury] save_epg_cache error: %s" % e)

    def epg_cache_put_events(self, service_ref, title_lang, descr_lang, events):
        """Persist translated EPG events for a service so they survive channel zapping."""
        try:
            sref = self._to_sref_str(service_ref)
            langkey = self._epg_lang_key(title_lang, descr_lang)
            now = int(time.time())
            with self._epg_cache_lock:
                srv = self.epg_cache.get(sref) or {}
                bucket = srv.get(langkey) or {"ts": now, "events": {}}
                evmap = bucket.get("events") or {}
                for ev in (events or []):
                    try:
                        begin = ev[0]
                        duration = ev[1]
                        name = ev[2] if len(ev) > 2 else ""
                        short = ev[3] if len(ev) > 3 else ""
                        longd = ev[4] if len(ev) > 4 else ""
                        event_id = ev[5] if len(ev) > 5 else 0
                        k = self._epg_event_key(event_id, begin, duration)
                        evmap[k] = {"t": name, "s": short, "l": longd}
                    except Exception:
                        continue
                bucket["ts"] = now
                bucket["events"] = evmap
                srv[langkey] = bucket
                self.epg_cache[sref] = srv

            try:
                self._epg_cache_dirty = getattr(self, "_epg_cache_dirty", 0) + 1
            except Exception:
                self._epg_cache_dirty = 1

            interval = getattr(self, "_epg_cache_save_interval", 0) or 0
            if interval and self._epg_cache_dirty >= interval:
                self._save_epg_cache()

            # Force flush to disk for manual 'Translate Current Event/EPG' so cache file appears immediately
            if getattr(self, "_epg_cache_force_save", False):
                try:
                    self._epg_cache_force_save = False
                except Exception:
                    pass
                try:
                    self._save_epg_cache(force=True)
                except Exception:
                    pass
        except Exception as e:
            try:
                self._log("epg_cache_put_events error: %s" % e)
            except Exception:
                pass


    def epg_cache_put_original_events(self, service_ref, events):
        """Persist ORIGINAL (provider) EPG texts for events we translated, so we can revert later."""
        try:
            sref = self._to_sref_str(service_ref)
            now = int(time.time())
            with self._epg_cache_lock:
                srv = self.epg_cache.get(sref) or {}
                bucket = srv.get("__orig__") or {"ts": now, "events": {}}
                evmap = bucket.get("events") or {}
                for ev in (events or []):
                    try:
                        begin = ev[0]
                        duration = ev[1]
                        name = ev[2] if len(ev) > 2 else ""
                        short = ev[3] if len(ev) > 3 else ""
                        longd = ev[4] if len(ev) > 4 else ""
                        event_id = ev[5] if len(ev) > 5 else 0
                        k = self._epg_event_key(event_id, begin, duration)
                        evmap[k] = {"t": name, "s": short, "l": longd}
                    except Exception:
                        continue
                bucket["ts"] = now
                bucket["events"] = evmap
                srv["__orig__"] = bucket
                self.epg_cache[sref] = srv

            try:
                self._epg_cache_dirty = getattr(self, "_epg_cache_dirty", 0) + 1
            except Exception:
                self._epg_cache_dirty = 1
        except Exception:
            pass

    def _epg_cache_get_original_bucket(self, service_ref):
        try:
            sref = self._to_sref_str(service_ref)
            with self._epg_cache_lock:
                srv = self.epg_cache.get(sref) or {}
                bucket = srv.get("__orig__") or {}
                evmap = bucket.get("events") or {}
            return (sref, evmap)
        except Exception:
            return (None, {})

    def restore_original_epg_from_cache(self, service_ref):
        """Import ORIGINAL EPG texts for a service (if saved) back into eEPGCache (RAM)."""
        try:
            if not self.enabled:
                return 0

            sref_str, evmap = self._epg_cache_get_original_bucket(service_ref)
            if not sref_str or not evmap:
                return 0

            events_out = []
            for k, v in evmap.items():
                try:
                    parts = (k or "").split("|")
                    event_id = int(parts[0]) if len(parts) > 0 and parts[0] else 0
                    begin = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                    dur = int(parts[2]) if len(parts) > 2 and parts[2] else 0
                    name = (v or {}).get("t", "") or ""
                    short = (v or {}).get("s", "") or ""
                    longd = (v or {}).get("l", "") or ""
                    events_out.append((begin, dur, name, short, longd, event_id))
                except Exception:
                    continue

            if not events_out:
                return 0

            try:
                events_out.sort(key=lambda x: int(x[0] or 0))
            except Exception:
                pass

            try:
                epg = eEPGCache.getInstance()
            except Exception:
                epg = None
            if epg is None:
                return 0

            fn_import_events = getattr(epg, "importEvents", None)
            fn_import_event = getattr(epg, "importEvent", None)

            if fn_import_events is not None:
                try:
                    fn_import_events(sref_str, tuple(events_out))
                    return len(events_out)
                except Exception:
                    pass

            if fn_import_event is not None:
                ok = 0
                for x in events_out:
                    try:
                        try:
                            fn_import_event(sref_str, (x,))
                        except Exception:
                            fn_import_event(sref_str, x)
                        ok += 1
                    except Exception:
                        continue
                return ok

            return 0
        except Exception:
            return 0

    def clear_epg_translation_cache(self):
        """Clear persistent translated EPG cache (Translate Current Event / auto translate)."""
        try:
            with self._epg_cache_lock:
                self.epg_cache = {}
            self._epg_cache_dirty = 0

            path = getattr(self, "epg_cachepath", "") or ""

            # Best-effort remove multiple known filenames (supports old/new naming)
            candidates = set()
            if path:
                candidates.add(path)

            try:
                base_dir = os.path.dirname(path) if path else (os.path.dirname(getattr(self, "cachepath", "") or "") or "/tmp")
            except Exception:
                base_dir = "/tmp"

            for fname in ("aifury_epg_cache.json", "aifury_epg_on_cache", "aifury_epg_cache"):
                try:
                    candidates.add(os.path.join(base_dir, fname))
                except Exception:
                    pass

            for p in candidates:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass

    def revert_translated_epg_to_original(self, service_ref=None, keep_cache=False):
        """
        Revert translated EPG back to provider/original language.

        - If we have saved originals for a service, we import them back into eEPGCache (RAM).
        - If keep_cache is False (default), we also clear the persistent translated EPG cache file
          so translations are not re-applied automatically.
        - If keep_cache is True, translations remain saved on disk so they can be restored again
          when the feature is enabled.
        """
        try:
            if not self.enabled:
                if keep_cache:
                    return
                # Still clear cache so it won't auto-restore
                self.clear_epg_translation_cache()
                return

            # Build list of services to restore
            if service_ref is not None:
                srefs = [service_ref]
            else:
                with self._epg_cache_lock:
                    srefs = list((self.epg_cache or {}).keys())

            restored_any = 0
            for s in srefs:
                restored = 0
                try:
                    restored = int(self.restore_original_epg_from_cache(s) or 0)
                    restored_any += restored
                except Exception:
                    restored = 0

                # Best-effort: if we don't have stored originals for that service,
                # drop events so provider EPG can repopulate.
                if restored <= 0:
                    try:
                        epg = eEPGCache.getInstance()
                        fn_remove = getattr(epg, "removeEvents", None)
                        if fn_remove is not None:
                            try:
                                fn_remove(self._to_sref_str(s))
                            except Exception:
                                pass
                    except Exception:
                        pass

            # Only clear persistent translated EPG cache when explicitly requested.
            if not keep_cache:
                self.clear_epg_translation_cache()
        except Exception:
            if not keep_cache:
                try:
                    self.clear_epg_translation_cache()
                except Exception:
                    pass
    def epg_cache_lookup(self, service_ref, title_lang, descr_lang, event_id, begin, duration):
        try:
            sref = self._to_sref_str(service_ref)
            langkey = self._epg_lang_key(title_lang, descr_lang)
            k = self._epg_event_key(event_id, begin, duration)
            with self._epg_cache_lock:
                srv = self.epg_cache.get(sref) or {}
                bucket = srv.get(langkey) or {}
                evmap = bucket.get("events") or {}
                return evmap.get(k)
        except Exception:
            return None

    def restore_epg_from_cache(self, service_ref, title_lang=None, descr_lang=None):
        """
        Restore translated EPG events for a service from persistent epg_cache into eEPGCache (RAM).
        This makes translations survive EPG refreshes and channel zapping.
        Returns True if something was imported.
        """
        try:
            if not self.enabled:
                return False

            if title_lang is None:
                title_lang = config.plugins.aifury.epg_title_lang.value
            if descr_lang is None:
                descr_lang = config.plugins.aifury.epg_title_lang.value

            if not (title_lang or descr_lang):
                # Fallback: use the general plugin language selection if user didn't set EPG title/descr languages.
                try:
                    title_lang = (getattr(config.plugins.aifury, "language").value or "").strip()
                except Exception:
                    title_lang = ""
                descr_lang = title_lang
            if not (title_lang or descr_lang):
                return False

            sref_str = self._to_sref_str(service_ref)
            langkey = self._epg_lang_key(title_lang, descr_lang)

            with self._epg_cache_lock:
                srv = self.epg_cache.get(sref_str) or {}
                bucket = srv.get(langkey) or {}
                evmap = bucket.get("events") or {}

            if not evmap:
                return False

            events_out = []
            for k, v in evmap.items():
                try:
                    parts = (k or "").split("|")
                    event_id = int(parts[0]) if len(parts) > 0 and parts[0] else 0
                    begin = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                    dur = int(parts[2]) if len(parts) > 2 and parts[2] else 0
                    name = (v or {}).get("t", "") or ""
                    short = (v or {}).get("s", "") or ""
                    longd = (v or {}).get("l", "") or ""
                    events_out.append((begin, dur, name, short, longd, event_id))
                except Exception:
                    continue

            if not events_out:
                return False

            try:
                events_out.sort(key=lambda x: int(x[0] or 0))
            except Exception:
                pass

            try:
                epg = eEPGCache.getInstance()
            except Exception:
                epg = None
            if epg is None:
                return False

            fn_import_events = getattr(epg, "importEvents", None)
            fn_import_event = getattr(epg, "importEvent", None)

            longdesc_days = 0
            try:
                longdesc_days = int("0" or 0)
            except Exception:
                longdesc_days = 0

            def _maybe_blank_long(ev_tuple):
                if not longdesc_days:
                    return ev_tuple
                try:
                    if ev_tuple[0] > (time.time() + 86400 * int(longdesc_days)):
                        ev_list2 = list(ev_tuple)
                        if len(ev_list2) > 4:
                            ev_list2[4] = ""
                        return tuple(ev_list2)
                except Exception:
                    pass
                return ev_tuple

            try:
                if fn_import_events is not None:
                    fn_import_events(sref_str, tuple(_maybe_blank_long(x) for x in events_out))
                    return True
            except Exception:
                pass

            if fn_import_event is not None:
                ok = 0
                for x in events_out:
                    try:
                        fn_import_event(sref_str, _maybe_blank_long(x))
                        ok += 1
                    except Exception:
                        continue
                return ok > 0

            return False
        except Exception as e:
            print("[AIFury] restore_epg_from_cache error: %s" % e)
            return False

    def schedule_epg_restore(self, service_ref, delay_ms=350):
        """Schedule restoring translated EPG for a service shortly after zapping."""
        try:
            if hasattr(config.plugins.aifury, "enable_translate_current_event") and (not config.plugins.aifury.enable_translate_current_event.value):
                return
            if not self.enabled:
                return
            if not hasattr(config.plugins.aifury, "auto_restore_epg"):
                return
            if not config.plugins.aifury.auto_restore_epg.value:
                return
            # Determine languages; fall back to the general plugin language selection if needed.
            try:
                _tl = (config.plugins.aifury.epg_title_lang.value or "").strip()
            except Exception:
                _tl = ""
            try:
                _dl = (config.plugins.aifury.epg_title_lang.value or "").strip()
            except Exception:
                _dl = ""
            if not (_tl or _dl):
                try:
                    _tl = (getattr(config.plugins.aifury, "language").value or "").strip()
                except Exception:
                    _tl = ""
                _dl = _tl
            if not (_tl or _dl):
                return

            self._restore_ref = service_ref
            if self._restore_timer is not None:
                try:
                    self._restore_timer.start(int(delay_ms or 0), True)
                    return
                except Exception:
                    pass

            self.restore_epg_from_cache(service_ref)
        except Exception:
            pass

    def _restore_epg_from_cache_cb(self):
        try:
            ref = getattr(self, "_restore_ref", None)
            self._restore_ref = None
            if ref is None:
                return
            self.restore_epg_from_cache(ref)
        except Exception:
            pass




    # ---------- Bulk restore/revert (multi-service) ----------

    def _keep_restore_epg_cb(self):
        """Periodically re-apply translated EPG for the currently playing service."""
        try:
            # Dynamic interval from setup (0 disables periodic keep-restore)
            try:
                self._keep_restore_interval_ms = self._get_keep_restore_interval_ms(default_sec=90)
            except Exception:
                pass
            if int(getattr(self, '_keep_restore_interval_ms', 0) or 0) <= 0:
                try:
                    if getattr(self, '_keep_restore_timer', None) is not None:
                        self._keep_restore_timer.stop()
                except Exception:
                    pass
                return

            if not self.enabled:
                return
            if not hasattr(config.plugins.aifury, "auto_restore_epg"):
                return
            if not config.plugins.aifury.auto_restore_epg.value:
                return

            title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
            descr_lang = title_lang
            if not (title_lang or descr_lang):
                # Fallback to the general language selection
                try:
                    title_lang = (getattr(config.plugins.aifury, "language").value or "").strip()
                except Exception:
                    title_lang = ""
                descr_lang = title_lang
            if not (title_lang or descr_lang):
                return

            ref = None
            try:
                ref = self.session.nav.getCurrentlyPlayingServiceReference()
            except Exception:
                ref = None
            if ref is not None:
                # Warm translation for new/updated events (honors auto_translate_epg + min-gap).
                try:
                    self.schedule_auto_translate(ref, delay_ms=0, force=False)
                except Exception:
                    pass
                # Re-import translated events (handles overwrite/revert on EIT refresh).
                try:
                    self.restore_epg_from_cache(ref, title_lang=title_lang, descr_lang=descr_lang)
                except Exception:
                    pass
        finally:
            # Re-arm the timer (if available)
            try:
                if getattr(self, "_keep_restore_timer", None) is not None:
                    interval_ms = int(getattr(self, "_keep_restore_interval_ms", 0) or 0)
                    if interval_ms > 0:
                        self._timer_start_compat(self._keep_restore_timer, interval_ms)
                    else:
                        try:
                            self._keep_restore_timer.stop()
                        except Exception:
                            pass
            except Exception:
                pass


    def _timer_start_compat(self, tmr, delay_ms=0):
        """Start an eTimer in a way that works across images (start(ms, True) vs start(ms))."""
        if tmr is None:
            return
        try:
            tmr.start(int(delay_ms or 0), True)
            return
        except Exception:
            pass

    def _get_keep_restore_interval_ms(self, default_sec=0):
        """Read periodic keep-restore interval from config (seconds) and return milliseconds."""
        try:
            cfg = getattr(config.plugins.aifury, "keep_restore_interval", None)
            raw = cfg.value if cfg is not None else None
            sec = int(str(raw or default_sec).strip())
        except Exception:
            sec = int(default_sec or 0)
        if sec <= 0:
            return 0
        # Clamp to avoid pathological values
        if sec < 10:
            sec = 10
        if sec > 3600:
            sec = 3600
        return int(sec * 1000)
        try:
            tmr.start(int(delay_ms or 0))
            return
        except Exception:
            pass
        try:
            # Some images only expose startLongTimer(seconds)
            tmr.startLongTimer(0)
            return
        except Exception:
            pass

    def schedule_restore_all_epg_from_cache(self, delay_ms=0, title_lang=None, descr_lang=None):
        """
        Restore translated EPG for ALL services that exist in the persistent ON-cache.
        Runs in small batches to keep GUI responsive.
        """
        try:
            if not self.enabled:
                return
            if hasattr(config.plugins.aifury, "enable_translate_current_event") and (not config.plugins.aifury.enable_translate_current_event.value):
                return

            if getattr(self, '_bulk_epg_timer', None) is None:
                # fallback (may block if big list)
                with self._epg_cache_lock:
                    srefs = list((self.epg_cache or {}).keys())
                for s in srefs:
                    try:
                        self.restore_epg_from_cache(s, title_lang=title_lang, descr_lang=descr_lang)
                    except Exception:
                        pass
                return

            with self._epg_cache_lock:
                srefs = list((self.epg_cache or {}).keys())

            # De-dup + keep order
            try:
                seen = set()
                srefs2 = []
                for s in srefs:
                    if s in seen:
                        continue
                    seen.add(s)
                    srefs2.append(s)
                srefs = srefs2
            except Exception:
                pass

            self._bulk_epg_queue = list(srefs)
            self._bulk_epg_mode = "restore"
            self._bulk_title_lang = title_lang
            self._bulk_descr_lang = descr_lang
            self._timer_start_compat(getattr(self, '_bulk_epg_timer', None), int(delay_ms or 0))
        except Exception:
            pass

    def schedule_revert_all_translated_epg_to_original(self, delay_ms=0):
        """
        Revert ALL services back to ORIGINAL texts (provider language) using the saved __orig__ bucket.
        Keeps the ON-cache on disk, so enabling again can restore translations instantly.
        """
        try:
            if not self.enabled:
                return

            if getattr(self, '_bulk_epg_timer', None) is None:
                # fallback (may block if big list)
                with self._epg_cache_lock:
                    srefs = list((self.epg_cache or {}).keys())
                for s in srefs:
                    try:
                        self.restore_original_epg_from_cache(s)
                    except Exception:
                        pass
                return

            with self._epg_cache_lock:
                srefs = list((self.epg_cache or {}).keys())

            # De-dup + keep order
            try:
                seen = set()
                srefs2 = []
                for s in srefs:
                    if s in seen:
                        continue
                    seen.add(s)
                    srefs2.append(s)
                srefs = srefs2
            except Exception:
                pass

            self._bulk_epg_queue = list(srefs)
            self._bulk_epg_mode = "revert"
            self._bulk_title_lang = None
            self._bulk_descr_lang = None
            self._timer_start_compat(getattr(self, '_bulk_epg_timer', None), int(delay_ms or 0))
        except Exception:
            pass

    def _bulk_epg_cb(self):
        """Timer callback: process a few services per tick."""
        try:
            # Stop conditions
            if not self.enabled:
                self._bulk_epg_queue = []
                self._bulk_epg_mode = None
                return

            mode = getattr(self, "_bulk_epg_mode", None)
            queue = getattr(self, "_bulk_epg_queue", None) or []
            if not mode or not queue:
                self._bulk_epg_mode = None
                self._bulk_epg_queue = []
                return

            # If we are restoring translations, but the feature is currently OFF -> stop.
            if mode == "restore":
                try:
                    if hasattr(config.plugins.aifury, "enable_translate_current_event") and (not config.plugins.aifury.enable_translate_current_event.value):
                        self._bulk_epg_queue = []
                        self._bulk_epg_mode = None
                        return
                except Exception:
                    pass

            batch = 8
            try:
                batch = int(getattr(self, "_bulk_epg_batch", 8) or 8)
            except Exception:
                batch = 8

            processed = 0
            while processed < batch and self._bulk_epg_queue:
                s = self._bulk_epg_queue.pop(0)
                processed += 1
                if not s:
                    continue
                if mode == "restore":
                    try:
                        self.restore_epg_from_cache(s, title_lang=getattr(self, "_bulk_title_lang", None), descr_lang=getattr(self, "_bulk_descr_lang", None))
                    except Exception:
                        pass
                elif mode == "revert":
                    try:
                        self.restore_original_epg_from_cache(s)
                    except Exception:
                        pass

            if self._bulk_epg_queue:
                self._timer_start_compat(getattr(self, '_bulk_epg_timer', None), 60)
            else:
                self._bulk_epg_mode = None
        except Exception:
            try:
                self._bulk_epg_mode = None
                self._bulk_epg_queue = []
            except Exception:
                pass

    # ---------- Auto translate (background) ----------

    def schedule_auto_translate(self, service_ref, delay_ms=1200, force=False):
        """Schedule background EPG translation for a service after zapping or after settings changes."""
        try:
            if hasattr(config.plugins.aifury, "enable_translate_current_event") and (not config.plugins.aifury.enable_translate_current_event.value):
                return
            if not self.enabled:
                return
            if not hasattr(config.plugins.aifury, "auto_translate_epg"):
                return
            if not config.plugins.aifury.auto_translate_epg.value:
                return

            title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
            descr_lang = title_lang

            if not (title_lang or descr_lang):
                return

            # Do not spam translation for the same service/lang
            try:
                min_gap = int(config.plugins.aifury.auto_translate_min_gap.value or "15")
            except Exception:
                min_gap = 15

            sref_str = None
            try:
                sref_str = service_ref.toString() if hasattr(service_ref, "toString") else str(service_ref)
            except Exception:
                sref_str = str(service_ref)

            langkey = "%s|%s" % (title_lang or "", descr_lang or "")
            now = time.time()

            if not force:
                try:
                    with self._auto_tr_lock:
                        last = self._auto_tr_last.get((sref_str, langkey), 0)
                    if last and (now - last) < (min_gap * 60):
                        return
                except Exception:
                    pass

            # Mark as running soon (avoid duplicate scheduling)
            try:
                with self._auto_tr_lock:
                    self._auto_tr_last[(sref_str, langkey)] = now
            except Exception:
                pass

            self._auto_tr_ref = (service_ref, force)
            if self._auto_tr_timer is not None:
                try:
                    self._auto_tr_timer.start(int(delay_ms or 0), True)
                    return
                except Exception:
                    pass

            # Fallback: run immediately in background
            self._submit_bg(lambda: self._auto_translate_run(service_ref, force=force))
        except Exception:
            pass

    def _auto_translate_cb(self):
        try:
            payload = getattr(self, "_auto_tr_ref", None)
            self._auto_tr_ref = None
            if not payload:
                return
            ref, force = payload
            self._submit_bg(lambda: self._auto_translate_run(ref, force=force))
        except Exception:
            pass

    def _auto_translate_run(self, service_ref, force=False):
        try:
            if not self.enabled:
                return
            if not hasattr(config.plugins.aifury, "auto_translate_epg"):
                return
            if not config.plugins.aifury.auto_translate_epg.value:
                return

            title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
            descr_lang = title_lang

            if not (title_lang or descr_lang):
                return

            try:
                horizon = int(config.plugins.aifury.auto_translate_horizon.value or "1440")
            except Exception:
                horizon = 1440

            try:
                max_events = int(config.plugins.aifury.auto_translate_max_events.value or "250")
            except Exception:
                max_events = 250

            try:
                longdesc_days = int("0" or "0")
            except Exception:
                longdesc_days = 0

            # Run translation + import to RAM + persist to disk cache
            summary = self.epg_translate_service(
                service_ref,
                title_lang,
                descr_lang,
                longdesc_days=longdesc_days,
                horizon_minutes=horizon,
                max_events=max_events,
                progress_cb=None,
            )
            try:
                # Update background translation stats
                self._update_bg_stats(self._to_sref_str(service_ref), summary)
            except Exception:
                pass
            try:
                # Ensure UI uses the translated copy even if provider refreshes EPG shortly after
                self.schedule_epg_restore(service_ref, delay_ms=0)
            except Exception:
                pass

            try:
                self._log("auto_translate_epg summary: %s" % summary)
            except Exception:
                pass
        except Exception as e:
            try:
                self._log("auto_translate_epg error: %s" % e)
            except Exception:
                pass

    # ---------- Bouquet translation job (background, with notification) ----------
    def _fmt_int(self, n):
        """Format integer with thousands separators for notifications."""
        try:
            return "{:,}".format(int(n))
        except Exception:
            try:
                return str(n)
            except Exception:
                return "0"


    def _notify_global(self, text, timeout=6, msgtype=None):
        try:
            if msgtype is None:
                msgtype = 0
        except Exception:
            msgtype = 0

        t = 6
        try:
            t = int(timeout)
        except Exception:
            try:
                t = int(str(timeout or "6").strip())
            except Exception:
                t = 6

        # If t > 0 => auto-hide (popup). If t <= 0 => sticky (until dismissed).
        if t > 0:
            try:
                from Tools import Notifications  # type: ignore
                try:
                    Notifications.AddPopup(text, msgtype, int(t), "AIFury")
                    return
                except Exception:
                    pass
            except Exception:
                pass

            # Fallback: MessageBox notification (still auto-hides).
            try:
                from Tools import Notifications  # type: ignore
                from Screens.MessageBox import MessageBox
                try:
                    Notifications.AddNotification(MessageBox, text, msgtype, timeout=int(t))
                    return
                except Exception:
                    try:
                        Notifications.AddNotification(MessageBox, text, msgtype)
                        return
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            # Sticky notification: no timeout.
            try:
                from Tools import Notifications  # type: ignore
                from Screens.MessageBox import MessageBox
                try:
                    Notifications.AddNotification(MessageBox, text, msgtype)
                    return
                except Exception:
                    try:
                        Notifications.AddNotification(MessageBox, text, msgtype, timeout=0)
                        return
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            self._log("[notify] %s" % text)
        except Exception:
            pass

    def start_bouquet_bg_job(self, bouq_ref, title_lang, descr_lang, longdesc_days=0):
        """
        Start translating ALL services in a bouquet in background.
        Runs sequentially (one service at a time) using the controller thread pool and posts a completion notification.
        Returns True if started.
        """
        try:
            if getattr(self, "_bouquet_job_running", False):
                return False
        except Exception:
            pass

        bouq_ref = (bouq_ref or "").strip()
        if not bouq_ref:
            return False

        try:
            services = _list_services_in_bouquet(bouq_ref) or []
        except Exception:
            services = []
        if not services:
            self._notify_global("No services found in bouquet.", timeout=5)
            return False

        try:
            bn = _bouquet_name_from_ref(bouq_ref) or ""
        except Exception:
            bn = ""
        if not bn:
            bn = "Selected bouquet"

        # Mirror Translate Bouquet behavior: horizon >= 14 days, max_events unlimited.
        try:
            user_horizon = int(getattr(config.plugins.aifury, "auto_translate_horizon", None).value or "20160")
        except Exception:
            user_horizon = 20160
        horizon = max(user_horizon, 20160)
        max_events = 0

        # Conservative network override during bulk bouquet runs
        old_ov = getattr(self, "_net_override", None)
        try:
            timeout_s = max(8.0, float(getattr(config.plugins.aifury, "req_timeout", None).value or 0) or 0.0)
        except Exception:
            timeout_s = 8.0
        try:
            retries = max(3, int(getattr(config.plugins.aifury, "req_retries", None).value or 0) or 0)
        except Exception:
            retries = 3
        try:
            min_interval = max(0.75, float(getattr(self, "_min_interval", 0.0) or 0.0))
        except Exception:
            min_interval = 0.75
        try:
            self._bouquet_job_old_net_override = old_ov
            self._net_override = {"timeout_s": timeout_s, "retries": retries, "min_interval": min_interval}
        except Exception:
            self._bouquet_job_old_net_override = None

        # job state
        self._bouquet_job_running = True
        self._bouquet_job_name = bn
        self._bouquet_job_ref = bouq_ref
        self._bouquet_job_queue = list(services)
        self._bouquet_job_processing = False
        self._bouquet_job_title_lang = title_lang or ""
        self._bouquet_job_descr_lang = descr_lang or ""
        self._bouquet_job_longdesc_days = int(longdesc_days or 0)
        self._bouquet_job_horizon = int(horizon or 20160)
        self._bouquet_job_max_events = int(max_events or 0)
        self._bouquet_job_started_ts = time.time()
        self._bouquet_job_totals = {
            "services": 0,
            "total": 0,
            "translated": 0,
            "skipped": 0,
            "imported": 0,
            "err_title": 0,
            "err_short": 0,
            "err_long": 0,
            "read_errors": 0,
            "import_errors": 0,
        }

        # timer (main loop) to schedule sequential work
        if getattr(self, "_bouquet_job_timer", None) is None and eTimer is not None:
            try:
                self._bouquet_job_timer = eTimer()
                self._bouquet_job_timer.callback.append(self._bouquet_job_tick)
            except Exception:
                self._bouquet_job_timer = None


        try:
            if getattr(self, "_bouquet_job_timer", None) is not None:
                self._bouquet_job_timer.start(50, True)
            else:
                self._bouquet_job_tick()
        except Exception:
            try:
                self._bouquet_job_tick()
            except Exception:
                return False
        return True

    def _bouquet_job_finish(self):
        try:
            self._net_override = getattr(self, "_bouquet_job_old_net_override", None)
        except Exception:
            pass
        try:
            self._bouquet_job_running = False
            self._bouquet_job_processing = False
            self._bouquet_job_finished_ts = time.time()
        except Exception:
            pass

        try:
            totals = getattr(self, "_bouquet_job_totals", {}) or {}
            bn = getattr(self, "_bouquet_job_name", "") or "Bouquet"
            msg = [
                "Translation completed",
                "Bouquet: %s" % bn,
                "Services: %s" % self._fmt_int(totals.get("services", 0)),
                "Events translated: %s" % self._fmt_int((totals.get("imported", 0) or 0) if int(totals.get("imported", 0) or 0) > 0 else (totals.get("translated", 0) or 0)),
                "Skipped (empty EPG): %s" % self._fmt_int(totals.get("empty_epg", 0)),
                "Errors: %s" % self._fmt_int((totals.get("err_title", 0) or 0) + (totals.get("err_short", 0) or 0) + (totals.get("err_long", 0) or 0) + (totals.get("read_errors", 0) or 0) + (totals.get("import_errors", 0) or 0)),
            ]
            show_done = True
            try:
                show_done = bool(getattr(config.plugins.aifury, "bouquet_bg_notify_done", None).value)
            except Exception:
                show_done = True
            if show_done:
                _t = 10
                try:
                    _t = int(config.plugins.aifury.done_notify_timeout.value)
                except Exception:
                    _t = 10
                self._notify_global("\n".join(msg), timeout=_t)
        except Exception:
            pass

    def _bouquet_job_tick(self):
        try:
            if not getattr(self, "_bouquet_job_running", False):
                return
            if getattr(self, "_bouquet_job_processing", False):
                return
            q = getattr(self, "_bouquet_job_queue", None) or []
            if not q:
                self._bouquet_job_finish()
                return

            sref = q.pop(0)
            self._bouquet_job_queue = q
            self._bouquet_job_processing = True

            def _work():
                try:
                    return self.epg_translate_service(
                        sref,
                        getattr(self, "_bouquet_job_title_lang", ""),
                        getattr(self, "_bouquet_job_descr_lang", ""),
                        getattr(self, "_bouquet_job_longdesc_days", 0),
                        getattr(self, "_bouquet_job_horizon", 20160),
                        getattr(self, "_bouquet_job_max_events", 0),
                    )
                except Exception as e:
                    return {"import_error": "bg job error: %s" % e}

            def _done(summary):
                try:
                    totals = getattr(self, "_bouquet_job_totals", {}) or {}
                    totals["services"] = int(totals.get("services", 0) or 0) + 1
                    if isinstance(summary, dict):
                        for k in ["total", "translated", "skipped", "imported", "err_title", "err_short", "err_long"]:
                            try:
                                totals[k] = int(totals.get(k, 0) or 0) + int(summary.get(k, 0) or 0)
                            except Exception:
                                pass
                        if summary.get("read_error"):
                            totals["read_errors"] = int(totals.get("read_errors", 0) or 0) + 1
                            try:
                                _re = str(summary.get("read_error") or "")
                                if _re == "System returned empty EPG list" or "empty EPG" in _re:
                                    totals["empty_epg"] = int(totals.get("empty_epg", 0) or 0) + 1
                            except Exception:
                                pass
                        if summary.get("import_error"):
                            totals["import_errors"] = int(totals.get("import_errors", 0) or 0) + 1
                    self._bouquet_job_totals = totals
                except Exception:
                    pass

                try:
                    self._save_epg_cache(force=True)
                except Exception:
                    pass

                try:
                    self.schedule_epg_restore(sref, delay_ms=0)
                except Exception:
                    pass

                try:
                    self._bouquet_job_processing = False
                except Exception:
                    pass

                try:
                    if getattr(self, "_bouquet_job_timer", None) is not None:
                        self._bouquet_job_timer.start(50, True)
                    else:
                        self._bouquet_job_tick()
                except Exception:
                    pass

            try:
                pool = getattr(self, "_pool", None)
                if pool is not None:
                    fut = pool.submit(_work)

                    def _cb(_fut):
                        try:
                            res = _fut.result()
                        except Exception as e:
                            res = {"import_error": "bg job error: %s" % e}
                        try:
                            self._call_ui(lambda r=res: _done(r))
                        except Exception:
                            _done(res)

                    fut.add_done_callback(_cb)
                else:
                    import threading

                    def _thr():
                        res = _work()
                        try:
                            self._call_ui(lambda r=res: _done(r))
                        except Exception:
                            _done(res)

                    t = threading.Thread(target=_thr)
                    t.setDaemon(True)
                    t.start()
            except Exception:
                try:
                    self._call_ui(lambda: _done({"import_error": "failed to submit bg job task"}))
                except Exception:
                    _done({"import_error": "failed to submit bg job task"})
        except Exception:
            try:
                self._bouquet_job_processing = False
            except Exception:
                pass

    def clear_epg_cache(self):
        try:
            with self._epg_cache_lock:
                self.epg_cache = {}
            self._epg_cache_dirty = 0
            try:
                if getattr(self, "epg_cachepath", None) and os.path.exists(self.epg_cachepath):
                    os.remove(self.epg_cachepath)
            except Exception:
                pass
            print("[AIFury] epg cache cleared")
        except Exception:
            pass

    def translate_to_cached_or_async(self, text, lang):
        """Cached-only fast path for a specific language; warms cache asynchronously if missing."""
        if not text or not self.enabled or not lang:
            return text
        cache_key = "%s|%s" % (lang, text)
        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    return self.cache[cache_key]
        except Exception:
            pass

        # warm in background
        with self._pending_lock:
            if cache_key in self._pending:
                return text
            self._pending.add(cache_key)

        def worker():
            try:
                self.translate_to(text, lang)
            except Exception as e:
                try:
                    self._log("translate_to_cached_or_async worker error: %s" % e)
                except Exception:
                    pass
            finally:
                try:
                    with self._pending_lock:
                        if cache_key in self._pending:
                            self._pending.remove(cache_key)
                except Exception:
                    pass

        self._submit_bg(worker)
        return text

    def translate_to_async_with_callback(self, text, lang, callback):
        """Translate to a specific lang in background then callback(translated_text) on UI thread."""
        if not text or not self.enabled or not lang:
            return
        cache_key = "%s|%s" % (lang, text)
        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    self._call_ui(lambda: callback(self.cache.get(cache_key)))
                    return
        except Exception:
            pass

        with self._pending_lock:
            if cache_key in self._pending:
                return
            self._pending.add(cache_key)

        def worker():
            try:
                result = self.translate_to(text, lang)
                self._call_ui(lambda: callback(result))
            except Exception as e:
                try:
                    self._log("translate_to_async_with_callback error: %s" % e)
                except Exception:
                    pass
            finally:
                try:
                    with self._pending_lock:
                        if cache_key in self._pending:
                            self._pending.remove(cache_key)
                except Exception:
                    pass

        self._submit_bg(worker)
    # ---------- logging / ui helpers ----------

    def _log(self, msg):
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            line = "[%s] %s\n" % (ts, msg)
            with self._log_lock:
                d = os.path.dirname(self.logpath)
                if d and not os.path.isdir(d):
                    try:
                        os.makedirs(d)
                    except Exception:
                        pass
                with open(self.logpath, "a") as f:
                    f.write(line)
        except Exception:
            # لا نسمح للّوج أن يسبب مشاكل في التشغيل
            pass

    def _call_ui(self, fn):
        """Run fn on UI/main thread if possible (via eTimer), otherwise run directly."""
        try:
            if self._ui_timer is None:
                fn()
                return
            self._ui_queue.append(fn)
            self._ui_timer.start(0, True)
        except Exception:
            try:
                fn()
            except Exception:
                pass

    def _flush_ui_queue(self):
        try:
            q = self._ui_queue[:]
            self._ui_queue[:] = []
            for fn in q:
                try:
                    fn()
                except Exception:
                    pass
        except Exception:
            pass

    def _ensure_pool(self):
        """(Re)create worker pool when configuration changes."""
        try:
            size = int(config.plugins.aifury.workers.value or "0")
        except Exception:
            size = 0

        if size == getattr(self, "_pool_size", 0) and getattr(self, "_pool", None) is not None:
            return

        # tear down existing pool
        try:
            if getattr(self, "_pool", None) is not None:
                self._pool.stop()
        except Exception:
            pass

        self._pool_size = size
        self._pool = None

        try:
            if size > 0:
                self._pool = _AIFuryWorkerPool(workers=size, logger=self._log)
        except Exception:
            self._pool = None

    def _rate_limit(self, interval=None):
        """Simple global rate limit to reduce throttling/bans from translation providers."""
        try:
            if interval is None:
                interval = float(getattr(self, "_min_interval", 0.0) or 0.0)
            else:
                interval = float(interval or 0.0)
            if interval <= 0.0:
                return
            with self._rate_lock:
                now = time.time()
                wait = (self._last_req_ts + interval) - now
                if wait > 0:
                    time.sleep(wait)
                self._last_req_ts = time.time()
        except Exception:
            pass

    def _submit_bg(self, fn):
        """Submit background work to pool if available, otherwise spawn a daemon thread."""
        try:
            if getattr(self, "_pool", None) is not None:
                ok = self._pool.submit(fn)
                if ok:
                    return
        except Exception:
            pass

        try:
            t = threading.Thread(target=fn)
            t.setDaemon(True)
            t.start()
        except Exception:
            pass



    def translate_to(self, text, lang):
        """
        Translate to a specific language code (separate from the global UI language).
        If lang is empty/disabled, returns the original text.
        """
        if not text:
            return text
        if not lang:
            return text

        cache_key = "%s|%s" % (lang, text)

        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    return self.cache[cache_key]
        except Exception:
            pass

        if not self.enabled:
            return text

        translator = self._get_translator()
        if translator is None:
            return text

        # Respect global rate limit + timeout/retry settings
        timeout_s = None
        retries = 0
        try:
            timeout_s = float(config.plugins.aifury.req_timeout.value)
        except Exception:
            timeout_s = None
        try:
            retries = int(config.plugins.aifury.req_retries.value)
        except Exception:
            retries = 0

        # Optional per-operation network overrides (used by bulk/bouquet translation to improve completion).
        interval_override = None
        try:
            ov = getattr(self, "_net_override", None)
            if isinstance(ov, dict) and ov:
                if "timeout_s" in ov and ov.get("timeout_s") is not None:
                    timeout_s = float(ov.get("timeout_s"))
                if "retries" in ov and ov.get("retries") is not None:
                    retries = int(ov.get("retries"))
                if "min_interval" in ov and ov.get("min_interval") is not None:
                    interval_override = float(ov.get("min_interval"))
        except Exception:
            interval_override = None

        last_err = None
        for attempt in range(max(0, retries) + 1):
            try:
                self._rate_limit(interval_override)
                out = translator(text, lang, timeout=timeout_s)
                # google_translate_api returns "" on failure; treat empty as failure to avoid blank EPG fields.
                if out is None:
                    raise ValueError("translation returned None")
                try:
                    if hasattr(out, "strip") and out.strip() == "":
                        raise ValueError("translation returned empty")
                except Exception:
                    # If strip fails for any reason, consider it a failure.
                    raise ValueError("translation result invalid")
                out = out.strip() if hasattr(out, "strip") else out
                try:
                    with self._cache_lock:
                        self.cache[cache_key] = out
                except Exception:
                    pass
                try:
                    if hasattr(self, "_save_cache_debounced"):
                        self._save_cache_debounced()
                    else:
                        self._save_cache()
                except Exception:
                    pass
                return out
            except Exception as e:
                last_err = e
                # exponential backoff with jitter
                try:
                    base = 0.25 * (2 ** attempt)
                    time.sleep(base + random.random() * 0.15)
                except Exception:
                    pass
                continue

        if last_err is not None:
            self._log("translate_to failed: %s" % last_err)
        return text

    def _epg_get_instance(self):
        try:
            if eEPGCache is None:
                return None
            return eEPGCache.getInstance()
        except Exception:
            return None

    def epg_translate_service(self, service_ref, title_lang, descr_lang, longdesc_days=0, horizon_minutes=20160, max_events=0, progress_cb=None):
        """
        Translate EPG for a single service reference and import results into EPGCache (RAM).
        Returns summary dict.
        - service_ref: eServiceReference or string ref
        - title_lang: language for event name ("" disables)
        - descr_lang: language for short/long descriptions ("" disables)
        """
        summary = {
            "total": 0,
            "translated": 0,
            "skipped": 0,
            "imported": 0,
            "err_title": 0,
            "err_short": 0,
            "err_long": 0,
            "read_error": "",
            "import_error": "",
        }

        if not (title_lang or descr_lang):
            summary["read_error"] = "No EPG translation language selected"
            return summary

        epg = self._epg_get_instance()
        if epg is None:
            summary["read_error"] = "EPGCache is not available on this image"
            return summary

        try:
            sref_str = service_ref.toString() if hasattr(service_ref, "toString") else str(service_ref)
            sref_lookup = sref_str.rstrip(":")
        except Exception:
            sref_str = str(service_ref)
            sref_lookup = sref_str.rstrip(":")

        try:
            # B=begin, D=duration, T=title, S=short, E=extended, I=eventId
            ev_list = epg.lookupEvent(["BDTSEI", (sref_lookup, 0, -1, int(horizon_minutes))])
            if not ev_list:
                summary["read_error"] = "System returned empty EPG list"
                return summary
        except Exception as e:
            summary["read_error"] = "EPG read error: %s" % e
            return summary

        if max_events:
            try:
                me = int(max_events)
                if me > 0 and len(ev_list) > me:
                    ev_list = ev_list[:me]
            except Exception:
                pass

        summary["total"] = len(ev_list)
        new_events = []
        orig_events = []

        for i, ev in enumerate(ev_list):
            try:
                # expected: (begin, duration, title, short, long, eventId)
                begin = ev[0]
                duration = ev[1]
                name = ev[2] if len(ev) > 2 else ""
                short = ev[3] if len(ev) > 3 else ""
                longd = ev[4] if len(ev) > 4 else ""
                event_id = ev[5] if len(ev) > 5 else 0
            except Exception:
                # unexpected structure; skip defensively
                summary["skipped"] += 1
                if progress_cb:
                    try:
                        progress_cb(i + 1, summary["total"], summary)
                    except Exception:
                        pass
                continue

            tr_name = name
            tr_short = short
            tr_long = longd

            if title_lang and name:
                try:
                    tr_name = self.translate_to(name, title_lang)
                except Exception:
                    summary["err_title"] += 1
            if descr_lang and short:
                try:
                    tr_short = self.translate_to(short, descr_lang)
                except Exception:
                    summary["err_short"] += 1
            if descr_lang and longd:
                try:
                    tr_long = self.translate_to(longd, descr_lang)
                except Exception:
                    summary["err_long"] += 1

            if (name, short, longd) != (tr_name, tr_short, tr_long):
                summary["translated"] += 1
                # Save original texts for later revert
                try:
                    orig_item = list(ev) if isinstance(ev, (list, tuple)) else [begin, duration, name, short, longd, event_id]
                    while len(orig_item) < 6:
                        orig_item.append(0)
                    orig_events.append(tuple(orig_item[:6]))
                except Exception:
                    pass
                # preserve tuple structure and append a marker at end for debug (ignored by importer if unsupported)
                item = list(ev)
                if len(item) >= 5:
                    item[2] = tr_name
                    item[3] = tr_short
                    item[4] = tr_long
                new_events.append(tuple(item))
            else:
                summary["skipped"] += 1

            if progress_cb:
                try:
                    progress_cb(i + 1, summary["total"], summary)
                except Exception:
                    pass

        if not new_events:
            return summary

        # Save original EPG texts for revert (only for events we changed)
        try:
            if orig_events:
                self.epg_cache_put_original_events(sref_str, orig_events)
        except Exception:
            pass

        # Save translated EPG events to persistent cache file (so it won't disappear after zapping)
        try:
            self.epg_cache_put_events(sref_str, title_lang, descr_lang, new_events)
            self._save_epg_cache()
        except Exception:
            pass

        # import to EPG cache (RAM)
        fn_import_events = getattr(epg, "importEvents", None)
        fn_import_event = getattr(epg, "importEvent", None)

        def _maybe_blank_long(ev_tuple):
            if not longdesc_days:
                return ev_tuple
            try:
                # begin time is index 0
                if ev_tuple[0] > (time.time() + 86400 * int(longdesc_days)):
                    ev_list2 = list(ev_tuple)
                    if len(ev_list2) > 4:
                        ev_list2[4] = ""
                    return tuple(ev_list2)
            except Exception:
                pass
            return ev_tuple

        try:
            # Prefer bulk if available; fallback per-event
            if fn_import_events is not None:
                try:
                    fn_import_events(sref_str, tuple(_maybe_blank_long(x) for x in new_events))
                    summary["imported"] = len(new_events)
                    return summary
                except Exception:
                    # Some images expect (ref, (event,)) per call
                    pass

            if fn_import_event is not None:
                ok = 0
                for x in new_events:
                    try:
                        x2 = _maybe_blank_long(x)
                        try:
                            fn_import_event(sref_str, (x2,))
                        except Exception:
                            fn_import_event(sref_str, x2)
                        ok += 1
                    except Exception:
                        continue
                summary["imported"] = ok
                return summary

            summary["import_error"] = "EPG import functions are not available on this image"
        except Exception as e:
            summary["import_error"] = "EPG import error: %s" % e

        return summary

    def translate_cached_or_async(self, text):
        """Return cached translation immediately; if not cached, warm cache asynchronously and return original text."""
        if not text or not self.enabled:
            return text
        lang = config.plugins.aifury.language.value
        cache_key = "%s|%s" % (lang, text)
        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    return self.cache[cache_key]
        except Exception:
            pass
        self.translate_async(text)
        return text

    def translate_async_with_callback(self, text, callback):
        """Translate in background then call callback(translated_text) on UI thread."""
        if not text or not self.enabled:
            return
        lang = config.plugins.aifury.language.value
        cache_key = "%s|%s" % (lang, text)

        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    self._call_ui(lambda: callback(self.cache.get(cache_key)))
                    return
        except Exception:
            pass

        with self._pending_lock:
            if cache_key in self._pending:
                return
            self._pending.add(cache_key)

        def worker():
            try:
                result = self.translate(text)
                self._call_ui(lambda: callback(result))
            except Exception as e:
                self._log("translate_async_with_callback error: %s" % e)
            finally:
                try:
                    with self._pending_lock:
                        if cache_key in self._pending:
                            self._pending.remove(cache_key)
                except Exception:
                    pass

        self._submit_bg(worker)

    # ---------- translation ----------
    def _get_translator(self):
        """Return translation callable.

        NOTE: google_translate_api.py has been merged into this file, so we no longer import it.
        The returned function must follow: fn(text, lang, timeout=None) -> translated_text or "" on failure.
        """
        def _do_translate(text, lang, timeout=None):
            try:
                # translate_text never raises to caller; returns "" on failure.
                return translate_text(
                    text,
                    target_lang=lang,
                    timeout=(timeout if timeout is not None else DEFAULT_TIMEOUT),
                    log_path=getattr(self, "logpath", None),
                    log_fn=getattr(self, "_log", None),
                    retries=0,
                    min_interval_ms=0,  # controller already rate-limits
                )
            except Exception as e:
                try:
                    if hasattr(self, "_log"):
                        self._log("translate_text internal error: %s" % e)
                except Exception:
                    pass
                return ""
        return _do_translate

    def translate(self, text):
        lang = config.plugins.aifury.language.value

        if not text:
            return text

        cache_key = "%s|%s" % (lang, text)

        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    return self.cache[cache_key]
        except Exception:
            pass

        if not self.enabled:
            return text

        translator = self._get_translator()
        if translator is None:
            return text

        _t0 = None

        # apply global rate limit before hitting provider
        self._rate_limit()

        timeout = None
        retries = 0
        try:
            timeout = int(config.plugins.aifury.req_timeout.value or "3")
        except Exception:
            timeout = None
        try:
            retries = int(config.plugins.aifury.req_retries.value or "0")
        except Exception:
            retries = 0

        last_err = None
        try:
            _t0 = time.time()
            for attempt in range(max(0, retries) + 1):
                try:
                    translated = translator(text, lang, timeout=timeout)
                    if translated is None:
                        return text
                    try:
                        if hasattr(translated, "strip") and translated.strip() == "":
                            return text
                    except Exception:
                        return text
                        return text
                    break
                except Exception as e:
                    last_err = e
                    if attempt >= retries:
                        raise
                    # exponential backoff with small jitter
                    try:
                        base = 0.35
                        time.sleep(base * (2 ** attempt) + random.random() * 0.15)
                    except Exception:
                        pass
        except Exception as e:
            print("[AIFury] error in translate provider: %s" % e)
            try:
                self._log("translate provider error: %s" % e)
            except Exception:
                pass
            return text

        try:
            with self._cache_lock:
                self.cache[cache_key] = translated
        except Exception:
            pass
        try:
            if _t0 is not None:
                dt = time.time() - _t0
                _sample = (text[:80] + "...") if len(text) > 80 else text
                self._log("translated (%.3fs) lang=%s text=%s" % (dt, lang, _sample))
        except Exception:
            pass

        try:
            max_items = getattr(self, "_cache_max_items", 0) or 0
            if max_items and len(self.cache) > max_items:
                self._shrink_cache(max_items)
        except Exception as e:
            print("[AIFury] cache soft-limit error: %s" % e)

        try:
            self._cache_dirty = getattr(self, "_cache_dirty", 0) + 1
        except Exception:
            self._cache_dirty = 1

        interval = getattr(self, "_cache_save_interval", 0) or 0
        if interval and self._cache_dirty >= interval:
            self._save_cache()

        return translated

    def translate_async(self, text):
        """Warm translation cache in background without blocking UI."""
        if not text or not self.enabled:
            return

        lang = config.plugins.aifury.language.value
        cache_key = "%s|%s" % (lang, text)

        try:
            with self._cache_lock:
                if cache_key in self.cache:
                    return
        except Exception:
            pass

        with self._pending_lock:
            if cache_key in self._pending:
                return
            self._pending.add(cache_key)

        def worker():
            try:
                self.translate(text)
            except Exception as e:
                self._log("translate_async worker error: %s" % e)
            finally:
                try:
                    with self._pending_lock:
                        if cache_key in self._pending:
                            self._pending.remove(cache_key)
                except Exception:
                    pass

        t = threading.Thread(target=worker)
        t.setDaemon(True)
        t.start()


    # ---------- monkey patches ----------

    def _patch_targets(self):
        # EventViewBase
        try:
            from Screens.EventView import EventViewBase

            if not hasattr(EventViewBase, "_aifury_patched"):
                print("[AIFury] Patching EventViewBase.setEvent")

                orig_setEvent = EventViewBase.setEvent

                def f_setEvent(this, event=None):
                    orig_setEvent(this, event)
                    try:
                        if not config.plugins.aifury.enable_translate_current_event.value:
                            return
                        if event is None:
                            return
                        name = event.getEventName() or ""
                        short = event.getShortDescription() or ""
                        extended = event.getExtendedDescription() or ""
                        description = short
                        if extended:
                            if description:
                                description += "\n\n" + extended
                            else:
                                description = extended

                        name_t = self.translate(name)
                        desc_t = self.translate(description)

                        try:
                            this.setTitle(name_t)
                        except Exception:
                            pass

                        for wname in ("epg_eventname", "event_name", "epg_name", "Service"):
                            try:
                                if wname in this:
                                    this[wname].setText(name_t)
                            except Exception:
                                pass

                        if desc_t:
                            for wname in (
                                "epg_description",
                                "description",
                                "epg_info",
                                "info",
                                "epg_extendeddescription",
                            ):
                                try:
                                    if wname in this:
                                        this[wname].setText(desc_t)
                                        break
                                except Exception:
                                    pass
                    except Exception as e:
                        print("[AIFury] patched EventViewBase.setEvent error: %s" % e)

                EventViewBase.setEvent = f_setEvent
                EventViewBase._aifury_patched = True
        except Exception as e:
            print("[AIFury] could not patch EventViewBase: %s" % e)

        # EventView
        try:
            from Screens.EventView import EventView

            if not hasattr(EventView, "_aifury_patched"):
                print("[AIFury] Patching EventView.setEvent (compat)")

                orig_setEvent2 = EventView.setEvent

                def f_setEvent2(this, service, event):
                    orig_setEvent2(this, service, event)
                    try:
                        if not config.plugins.aifury.enable_translate_current_event.value:
                            return
                        if event is None:
                            return
                        name = event.getEventName() or ""
                        short = event.getShortDescription() or ""
                        extended = event.getExtendedDescription() or ""
                        description = short
                        if extended:
                            if description:
                                description += "\n\n" + extended
                            else:
                                description = extended

                        ctrl = AIFuryController.instance
                        # عرض سريع بدون تهنيج أثناء التنقل:
                        # 1) لو فيه ترجمة محفوظة من "Translate Current Event" هنستخدمها فوراً
                        # 2) وإلا هنستخدم كاش/Async حسب لغة EPG المختارة
                        name_t = name
                        desc_t = description
                        title_lang = config.plugins.aifury.epg_title_lang.value
                        descr_lang = config.plugins.aifury.epg_title_lang.value

                        # حاول نستخرج بيانات الحدث (eventId/begin/duration) عشان نلاقيه في كاش EPG
                        ev_id = 0
                        ev_begin = 0
                        ev_dur = 0
                        try:
                            if hasattr(event, "getEventId"):
                                ev_id = event.getEventId() or 0
                        except Exception:
                            ev_id = 0
                        try:
                            if hasattr(event, "getBeginTime"):
                                ev_begin = event.getBeginTime() or 0
                        except Exception:
                            ev_begin = 0
                        try:
                            if hasattr(event, "getDuration"):
                                ev_dur = event.getDuration() or 0
                        except Exception:
                            ev_dur = 0

                        # service ref لاستخدامه في كاش EPG (EventView يمرر service كـ argument)
                        sref_obj = service
                        try:
                            # أحياناً بييجي service كـ tuple/list
                            if isinstance(service, (tuple, list)) and len(service) > 1:
                                sref_obj = service[1]
                        except Exception:
                            sref_obj = service

                        if ctrl is not None:
                            # عنوان الحدث
                            if title_lang and name:
                                hit = None
                                if sref_obj is not None:
                                    hit = ctrl.epg_cache_lookup(sref_obj, title_lang, descr_lang, ev_id, ev_begin, ev_dur)
                                if hit and hit.get("t"):
                                    name_t = hit.get("t")
                                else:
                                    name_t = ctrl.translate_to_cached_or_async(name, title_lang)
                            else:
                                name_t = ctrl.translate_cached_or_async(name)

                            # وصف الحدث
                            if descr_lang and description:
                                hit = None
                                if sref_obj is not None:
                                    hit = ctrl.epg_cache_lookup(sref_obj, title_lang, descr_lang, ev_id, ev_begin, ev_dur)
                                if hit:
                                    short_t = hit.get("s") or ""
                                    long_t = hit.get("l") or ""
                                    if short_t or long_t:
                                        desc_t = short_t
                                        if long_t:
                                            desc_t = (desc_t + "\n\n" + long_t) if desc_t else long_t
                                if desc_t == description:
                                    desc_t = ctrl.translate_to_cached_or_async(description, descr_lang)
                            else:
                                desc_t = ctrl.translate_cached_or_async(description)

                        try:
                            this.setTitle(name_t)
                        except Exception:
                            pass

                        if desc_t:
                            for wname in (
                                "epg_description",
                                "description",
                                "epg_info",
                                "info",
                                "epg_extendeddescription",
                            ):
                                try:
                                    if wname in this:
                                        this[wname].setText(desc_t)
                                        break
                                except Exception:
                                    pass

                    except Exception as e:
                        print("[AIFury] patched EventView.setEvent error: %s" % e)

                EventView.setEvent = f_setEvent2
                EventView._aifury_patched = True
        except Exception as e:
            print("[AIFury] could not patch EventView: %s" % e)

        # EPGList (translate event name inside EPG list widget)
        try:
            from Components.EpgList import EPGList

            if not hasattr(EPGList, "_aifury_patched"):
                print("[AIFury] Patching EPGList entries")

            # keep original methods
            orig_buildSingleEntry = getattr(EPGList, "buildSingleEntry", None)
            orig_buildMultiEntry = getattr(EPGList, "buildMultiEntry", None)

            # single EPG (one event per service)
            if orig_buildSingleEntry is not None:
                def f_buildSingleEntry(this, service, eventId, beginTime, duration, EventName):
                    try:
                        if not config.plugins.aifury.enable_translate_current_event.value:
                            return orig_buildSingleEntry(this, service, eventId, beginTime, duration, EventName)
                        ctrl = AIFuryController.instance
                        if ctrl is not None and EventName:
                            # Prefer persistent EPG translation cache (created by "Translate Current Event")
                            title_lang = config.plugins.aifury.epg_title_lang.value
                            descr_lang = config.plugins.aifury.epg_title_lang.value
                            if title_lang:
                                hit = ctrl.epg_cache_lookup(service, title_lang, descr_lang, eventId, beginTime, duration)
                                if hit and hit.get("t"):
                                    EventName = hit.get("t")
                                else:
                                    EventName = ctrl.translate_to_cached_or_async(EventName, title_lang)
                            else:
                                EventName = ctrl.translate_cached_or_async(EventName)
                    except Exception as e:
                        print("[AIFury] EPGList.buildSingleEntry translate error: %s" % e)
                    return orig_buildSingleEntry(this, service, eventId, beginTime, duration, EventName)

                EPGList.buildSingleEntry = f_buildSingleEntry

            # multi EPG (now/next style lists)
            if orig_buildMultiEntry is not None:
                def f_buildMultiEntry(this, changecount, service, eventId, beginTime, duration, EventName, nowTime, service_name):
                    try:
                        if not config.plugins.aifury.enable_translate_current_event.value:
                            return orig_buildMultiEntry(this, changecount, service, eventId, beginTime, duration, EventName, nowTime, service_name)
                        ctrl = AIFuryController.instance
                        if ctrl is not None:
                            title_lang = config.plugins.aifury.epg_title_lang.value
                            descr_lang = config.plugins.aifury.epg_title_lang.value
                            if title_lang and EventName:
                                hit = ctrl.epg_cache_lookup(service, title_lang, descr_lang, eventId, beginTime, duration)
                                if hit and hit.get("t"):
                                    EventName = hit.get("t")
                                else:
                                    EventName = ctrl.translate_to_cached_or_async(EventName, title_lang)
                            elif EventName:
                                EventName = ctrl.translate_cached_or_async(EventName)
                            # Uncomment if you want to translate service name as well:
                            # if service_name:
                            #     service_name = ctrl.translate(service_name)
                    except Exception as e:
                        print("[AIFury] EPGList.buildMultiEntry translate error: %s" % e)
                    return orig_buildMultiEntry(this, changecount, service, eventId, beginTime, duration, EventName, nowTime, service_name)

                EPGList.buildMultiEntry = f_buildMultiEntry

            EPGList._aifury_patched = True
        except Exception as e:
            print("[AIFury] could not patch EPGList: %s" % e)

        # Navigation: auto-restore translated EPG after zapping
        try:
            from Navigation import Navigation

            if not hasattr(Navigation, "_aifury_epgrestore_patched"):
                print("[AIFury] Patching Navigation.playService for EPG restore")

                orig_playService = Navigation.playService

                def f_playService(this, ref, *args, **kwargs):
                    ret = orig_playService(this, ref, *args, **kwargs)
                    try:
                        ctrl = AIFuryController.instance
                        if ctrl is not None:
                            ctrl.schedule_epg_restore(ref)
                            try:
                                ctrl.schedule_auto_translate(ref)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    return ret

                Navigation.playService = f_playService
                Navigation._aifury_epgrestore_patched = True
        except Exception as e:
            print("[AIFury] could not patch Navigation.playService: %s" % e)

        # EPGSelection
        try:
            from Screens.EpgSelection import EPGSelection

            if not hasattr(EPGSelection, "_aifury_patched"):
                print("[AIFury] Patching EPGSelection.onSelectionChanged")

            orig_onSel = EPGSelection.onSelectionChanged

            def f_onSelectionChanged(this):
                orig_onSel(this)
                try:
                    if not config.plugins.aifury.enable_translate_current_event.value:
                        return
                    if "list" not in this:
                        return
                    cur = this["list"].getCurrent()
                    if not cur:
                        return
                    event = cur[0]
                    if not event:
                        return
                    name = event.getEventName() or ""
                    short = event.getShortDescription() or ""
                    extended = event.getExtendedDescription() or ""
                    description = short
                    if extended:
                        if description:
                            description += "\n\n" + extended
                        else:
                            description = extended

                    ctrl = AIFuryController.instance
                    # عرض سريع بدون تهنيج أثناء التنقل:
                    # 1) لو فيه ترجمة محفوظة من "Translate Current Event" هنستخدمها فوراً
                    # 2) وإلا هنستخدم كاش/Async حسب لغة EPG المختارة
                    name_t = name
                    desc_t = description
                    title_lang = config.plugins.aifury.epg_title_lang.value
                    descr_lang = config.plugins.aifury.epg_title_lang.value

                    # حاول نستخرج بيانات الحدث (eventId/begin/duration) عشان نلاقيه في كاش EPG
                    ev_id = 0
                    ev_begin = 0
                    ev_dur = 0
                    try:
                        if hasattr(event, "getEventId"):
                            ev_id = event.getEventId() or 0
                    except Exception:
                        ev_id = 0
                    try:
                        if hasattr(event, "getBeginTime"):
                            ev_begin = event.getBeginTime() or 0
                    except Exception:
                        ev_begin = 0
                    try:
                        if hasattr(event, "getDuration"):
                            ev_dur = event.getDuration() or 0
                    except Exception:
                        ev_dur = 0

                    # service ref من tuple الحالي لو موجود
                    sref_obj = None
                    try:
                        if isinstance(cur, (tuple, list)) and len(cur) > 1:
                            sref_obj = cur[1]
                    except Exception:
                        sref_obj = None

                    if ctrl is not None:
                        # عنوان الحدث
                        if title_lang and name:
                            hit = None
                            if sref_obj is not None:
                                hit = ctrl.epg_cache_lookup(sref_obj, title_lang, descr_lang, ev_id, ev_begin, ev_dur)
                            if hit and hit.get("t"):
                                name_t = hit.get("t")
                            else:
                                name_t = ctrl.translate_to_cached_or_async(name, title_lang)
                        else:
                            name_t = ctrl.translate_cached_or_async(name)

                        # وصف الحدث
                        if descr_lang and description:
                            hit = None
                            if sref_obj is not None:
                                hit = ctrl.epg_cache_lookup(sref_obj, title_lang, descr_lang, ev_id, ev_begin, ev_dur)
                            if hit:
                                short_t = hit.get("s") or ""
                                long_t = hit.get("l") or ""
                                if short_t or long_t:
                                    desc_t = short_t
                                    if long_t:
                                        desc_t = (desc_t + "\n\n" + long_t) if desc_t else long_t
                            if desc_t == description:
                                desc_t = ctrl.translate_to_cached_or_async(description, descr_lang)
                        else:
                            desc_t = ctrl.translate_cached_or_async(description)

                    for wname in ("epg_eventname", "event_name", "Service"):
                        try:
                            if wname in this:
                                this[wname].setText(name_t)
                        except Exception:
                            pass

                    if desc_t:
                        for wname in (
                            "epg_description",
                            "key_info",
                            "epg_info",
                            "description",
                        ):
                            try:
                                if wname in this:
                                    this[wname].setText(desc_t)
                                    break
                            except Exception:
                                pass

                    # ترجمة فورية عند الوقوف على القناة/الحدث (Debounce) بدون تهنيج أثناء التنقل
                    try:
                        ctrl = AIFuryController.instance
                        if ctrl is not None and ctrl.enabled:
                            # استخدم لغات EPG لو مفعّلة، وإلا استخدم اللغة العامة
                            title_lang = config.plugins.aifury.epg_title_lang.value or config.plugins.aifury.language.value
                            descr_lang = config.plugins.aifury.epg_title_lang.value or config.plugins.aifury.language.value
                            key_name = "%s|%s" % (title_lang, name or "")
                            key_desc = "%s|%s" % (descr_lang, description or "")
                            this._aifury_cur_keys = (key_name, key_desc)

                            if not hasattr(this, "_aifury_sel_timer") and eTimer is not None:
                                this._aifury_sel_timer = eTimer()

                                def _fire():
                                    try:
                                        cb = getattr(this, "_aifury_sel_cb", None)
                                        if cb:
                                            cb()
                                    except Exception as ee:
                                        try:
                                            ctrl._log("EPGSelection debounce fire error: %s" % ee)
                                        except Exception:
                                            pass

                                this._aifury_sel_timer.callback.append(_fire)

                            def _do_async_update():
                                keys = getattr(this, "_aifury_cur_keys", None)
                                if not keys:
                                    return
                                expected_name, expected_desc = keys

                                def _upd_name(val):
                                    if getattr(this, "_aifury_cur_keys", None) != keys:
                                        return
                                    for wname2 in ("epg_eventname", "event_name", "Service"):
                                        try:
                                            if wname2 in this and val:
                                                this[wname2].setText(val)
                                        except Exception:
                                            pass

                                def _upd_desc(val):
                                    if getattr(this, "_aifury_cur_keys", None) != keys:
                                        return
                                    if not val:
                                        return
                                    for wname2 in ("epg_description", "key_info", "epg_info", "description"):
                                        try:
                                            if wname2 in this:
                                                this[wname2].setText(val)
                                                break
                                        except Exception:
                                            pass

                                # start async only if not cached
                                try:
                                    with ctrl._cache_lock:
                                        need_name = expected_name not in ctrl.cache
                                        need_desc = expected_desc not in ctrl.cache
                                except Exception:
                                    need_name = True
                                    need_desc = True

                                if need_name and name:
                                    try:
                                        if title_lang == config.plugins.aifury.language.value:
                                            ctrl.translate_async_with_callback(name, _upd_name)
                                        else:
                                            ctrl.translate_to_async_with_callback(name, title_lang, _upd_name)
                                    except Exception:
                                        ctrl.translate_async_with_callback(name, _upd_name)

                                if need_desc and description:
                                    try:
                                        if descr_lang == config.plugins.aifury.language.value:
                                            ctrl.translate_async_with_callback(description, _upd_desc)
                                        else:
                                            ctrl.translate_to_async_with_callback(description, descr_lang, _upd_desc)
                                    except Exception:
                                        ctrl.translate_async_with_callback(description, _upd_desc)

                            this._aifury_sel_cb = _do_async_update

                            if hasattr(this, "_aifury_sel_timer") and this._aifury_sel_timer is not None:
                                try:
                                    this._aifury_sel_timer.stop()
                                except Exception:
                                    pass
                                this._aifury_sel_timer.start(350, True)
                            else:
                                _do_async_update()
                    except Exception:
                        pass

                except Exception as e:
                    print("[AIFury] patched EPGSelection.onSelectionChanged error: %s" % e)

            EPGSelection.onSelectionChanged = f_onSelectionChanged
            EPGSelection._aifury_patched = True
        except Exception as e:
            print("[AIFury] could not patch EPGSelection: %s" % e)

        # ChannelSelection
        try:
            from Screens.ChannelSelection import ChannelSelection

            if not hasattr(ChannelSelection, "_aifury_patched"):
                print("[AIFury] Patching ChannelSelection.infoKeyPressed")

            orig_info = ChannelSelection.infoKeyPressed

            def f_infoKeyPressed(this):
                try:
                    orig_info(this)
                except Exception as e:
                    print("[AIFury] ChannelSelection.infoKeyPressed error: %s" % e)

            ChannelSelection.infoKeyPressed = f_infoKeyPressed
            ChannelSelection._aifury_patched = True
        except Exception as e:
            print("[AIFury] could not patch ChannelSelection: %s" % e)


    # ---------- stats helpers ----------

    def _update_bg_stats(self, sref_str, summary):
        """Accumulate stats for background auto-translate runs."""
        try:
            translated = 0
            imported = 0
            try:
                translated = int((summary or {}).get("translated", 0) or 0)
            except Exception:
                translated = 0
            try:
                imported = int((summary or {}).get("imported", 0) or 0)
            except Exception:
                imported = 0

            with self._bg_stats_lock:
                st = self._bg_stats or {}
                st["runs"] = int(st.get("runs", 0) or 0) + 1
                st["translated"] = int(st.get("translated", 0) or 0) + translated
                st["imported"] = int(st.get("imported", 0) or 0) + imported
                st["last_ts"] = int(time.time())
                st["last_service"] = str(sref_str or "")
                self._bg_stats = st
        except Exception:
            pass

    def get_bg_stats(self):
        """Return a copy of current background auto-translate stats."""
        try:
            with self._bg_stats_lock:
                return dict(self._bg_stats or {})
        except Exception:
            return {}

    def get_epg_cache_count(self, title_lang, descr_lang, service_refs=None):
        """Count translated EPG events currently stored in the ON-cache for the given languages.

        If service_refs is provided (list of service ref strings), count only those services.
        """
        try:
            langkey = self._epg_lang_key(title_lang, descr_lang)
            total = 0
            with self._epg_cache_lock:
                if not self.epg_cache:
                    return 0
                if service_refs is None:
                    for sref, srv in self.epg_cache.items():
                        bucket = (srv or {}).get(langkey) or {}
                        evmap = bucket.get("events") or {}
                        try:
                            total += len(evmap)
                        except Exception:
                            pass
                else:
                    for sref in (service_refs or []):
                        srv = (self.epg_cache or {}).get(sref) or {}
                        bucket = (srv or {}).get(langkey) or {}
                        evmap = bucket.get("events") or {}
                        try:
                            total += len(evmap)
                        except Exception:
                            pass
            return int(total)
        except Exception:
            return 0


# ---------- Info screen ----------


class AIFuryEpgTranslateProgress(Screen):
    """
    Translate current service EPG and import into EPGCache (RAM).
    This is an experimental feature; it relies on the availability of EPGCache import APIs in the image.
    """

    skin = (
        '<screen name="AIFuryEpgTranslateProgress" position="center,center" size="760,220" title="Translate Current Channel EPG">'
        '  <widget name="status" position="20,20" size="720,160" font="Regular;22" halign="left" valign="top" />'
        '  <eLabel text="EXIT = Cancel (stops UI only)" position="20,185" size="720,25" font="Regular;18" halign="center" />'
        '</screen>'
    )

    def __init__(self, session, ctrl, service_ref, title_lang, descr_lang, longdesc_days):
        Screen.__init__(self, session)
        self.session = session
        self.ctrl = ctrl
        self.service_ref = service_ref
        self.title_lang = title_lang
        self.descr_lang = descr_lang
        self.longdesc_days = longdesc_days

        self["status"] = Label("Preparing...")
        self["actions"] = ActionMap(
            ["OkCancelActions"],
            {
                "cancel": self.close,
                "ok": self.close,
            },
            -1,
        )

        self._state_lock = threading.Lock()
        self._done = False
        self._summary = None
        self._last_line = ""

        self._ui_timer = eTimer() if eTimer is not None else None
        if self._ui_timer is not None:
            try:
                self._ui_timer.callback.append(self._tick)
            except Exception:
                try:
                    self._ui_timer_conn = self._ui_timer.timeout.connect(self._tick)
                except Exception:
                    self._ui_timer = None

        self.onShown.append(self._start)

    def _start(self):
        if self._ui_timer is not None:
            try:
                self._ui_timer.start(250, False)
            except Exception:
                pass

        def _progress(done, total, summary):
            try:
                with self._state_lock:
                    self._summary = dict(summary)
                    self._summary["_done"] = int(done)
                    self._summary["_total"] = int(total)
            except Exception:
                pass

        def _run():
            try:
                summary = self.ctrl.epg_translate_service(
                    self.service_ref,
                    self.title_lang,
                    self.descr_lang,
                    longdesc_days=self.longdesc_days,
                    horizon_minutes=20160,
                    progress_cb=_progress,
                )
            except Exception as e:
                summary = {"read_error": "Unhandled error: %s" % e}
            with self._state_lock:
                self._done = True
                self._summary = summary
            self.ctrl._call_ui(self._finish)

        self.ctrl._submit_bg(_run)

    def _tick(self):
        try:
            with self._state_lock:
                s = dict(self._summary) if self._summary else {}
                done = s.get("_done", 0)
                total = s.get("_total", s.get("total", 0))
                translated = s.get("translated", 0)
                skipped = s.get("skipped", 0)
                imported = s.get("imported", 0)
                err_title = s.get("err_title", 0)
                err_short = s.get("err_short", 0)
                err_long = s.get("err_long", 0)
                read_error = s.get("read_error", "")
                import_error = s.get("import_error", "")
        except Exception:
            return

        lines = []
        if read_error:
            lines.append("EPG read: %s" % read_error)
        else:
            lines.append("Progress: %d / %d" % (done, total))
            lines.append("Translated: %d   |   Skipped: %d" % (translated, skipped))
            lines.append("Imported to RAM: %d" % imported)

        if err_title or err_short or err_long:
            lines.append("Errors - Title: %d   Short: %d   Long: %d" % (err_title, err_short, err_long))
        if import_error:
            lines.append("EPG import: %s" % import_error)

        txt = "\n".join(lines)
        if txt != self._last_line:
            self._last_line = txt
            try:
                self["status"].setText(txt)
            except Exception:
                pass

    def _finish(self):
        try:
            if self._ui_timer is not None:
                self._ui_timer.stop()
        except Exception:
            pass

        try:
            with self._state_lock:
                s = dict(self._summary) if self._summary else {}
        except Exception:
            s = {}

        # Show final summary
        msg_lines = []
        if s.get("read_error"):
            msg_lines.append("Failed: %s" % s.get("read_error"))
        else:
            msg_lines.append("Total events: %s" % s.get("total", 0))
            msg_lines.append("Translated: %s" % s.get("translated", 0))
            msg_lines.append("Skipped: %s" % s.get("skipped", 0))
            msg_lines.append("Imported: %s" % s.get("imported", 0))

        if s.get("import_error"):
            msg_lines.append("Import error: %s" % s.get("import_error"))

        if s.get("err_title") or s.get("err_short") or s.get("err_long"):
            msg_lines.append("Errors - Title: %s  Short: %s  Long: %s" % (s.get("err_title", 0), s.get("err_short", 0), s.get("err_long", 0)))

        try:
            _t = 8
            try:
                _t = int(config.plugins.aifury.done_notify_timeout.value)
            except Exception:
                _t = 8
            if _t <= 0:
                self.session.open(MessageBox, "\n".join(msg_lines), MessageBox.TYPE_INFO)
            else:
                self.session.open(MessageBox, "\n".join(msg_lines), MessageBox.TYPE_INFO, timeout=_t)
        except Exception:
            pass

        try:
            self.close()
        except Exception:
            pass


class AIFuryTranslateProgress(Screen):
    """
    Translate EPG for ALL services inside the Favourites bouquet and import to EPGCache (RAM),
    while persisting translations to the ON-cache.
    """

    skin = (
        '<screen name="AIFuryTranslateProgress" position="center,center" size="760,260" title="Translate Bouquet">'
        '  <widget name="status" position="20,20" size="720,200" font="Regular;22" halign="left" valign="top" />'
        '  <eLabel text="EXIT = Cancel" position="20,230" size="720,25" font="Regular;18" halign="center" />'
        '</screen>'
    )

    def __init__(self, session, ctrl, service_refs, title_lang, descr_lang, longdesc_days, bouquet_ref_str=""):
        Screen.__init__(self, session)
        self.ctrl = ctrl
        self.service_refs = list(service_refs or [])
        self.title_lang = title_lang or ""
        self.descr_lang = descr_lang or ""
        self.longdesc_days = int(longdesc_days or 0)
        self.bouquet_ref_str = bouquet_ref_str or ""
        self._stop = False
        self._done = False
        self._idx = 0
        self._total = len(self.service_refs)

        self["status"] = Label("Preparing...")

        self["actions"] = ActionMap(
            ["OkCancelActions"],
            {
                "cancel": self.keyCancel,
                "ok": self.keyCancel,
            },
            -2,
        )

        # Start background work
        self.ctrl._ensure_pool()
        self.ctrl._submit_bg(self._run)

    def keyCancel(self):
        self._stop = True
        try:
            self.close()
        except Exception:
            pass

    def _set_status(self, txt):
        try:
            self["status"].setText(txt)
        except Exception:
            pass

    def _run(self):
        # Translate ALL available EPG events per service (as much as the system provides).
        # We lift the horizon to at least 14 days and disable the max-events cap for bouquet runs.
        try:
            user_horizon = int(getattr(config.plugins.aifury, "auto_translate_horizon", None).value or "20160")
        except Exception:
            user_horizon = 20160
        horizon = max(user_horizon, 20160)

        # 0 = no limit inside epg_translate_service()
        max_events = 0

        # Bulk bouquet translation may trigger throttling; use conservative network settings during this run.
        _old_net_ov = getattr(self.ctrl, "_net_override", None)
        try:
            self.ctrl._net_override = {
                "timeout_s": max(8.0, float(config.plugins.aifury.req_timeout.value or 0) or 0),
                "retries": max(3, int(config.plugins.aifury.req_retries.value or 0) or 0),
                "min_interval": max(0.75, float(getattr(self.ctrl, "_min_interval", 0.0) or 0.0)),
            }
        except Exception:
            _old_net_ov = None

        totals = {"services": 0, "total": 0, "translated": 0, "skipped": 0, "imported": 0, "err_title": 0, "err_short": 0, "err_long": 0}
        try:
            bouquet_name = _bouquet_name_from_ref(self.bouquet_ref_str) or "Bouquet"
            for i, sref in enumerate(self.service_refs):
                if self._stop:
                    break

                name = _svc_name_from_ref(sref) or sref
                head = "Bouquet: %s\nService %d/%d\n%s\n" % (bouquet_name, i + 1, self._total, name)
                self.ctrl._call_ui(lambda h=head: self._set_status(h + "Starting..."))

                try:
                    summary = self.ctrl.epg_translate_service(
                        sref,
                        self.title_lang,
                        self.descr_lang,
                        longdesc_days=self.longdesc_days,
                        horizon_minutes=horizon,
                        max_events=max_events,
                        progress_cb=None,
                    )
                except Exception as e:
                    summary = {"import_error": "Unhandled error: %s" % e}

                totals["services"] += 1
                for k in ("total", "translated", "skipped", "imported", "err_title", "err_short", "err_long"):
                    try:
                        totals[k] += int(summary.get(k, 0) or 0)
                    except Exception:
                        pass

                line = "Events: %s  Translated: %s  Imported: %s\nErrors T/S/L: %s/%s/%s" % (
                    summary.get("total", 0),
                    summary.get("translated", 0),
                    summary.get("imported", 0),
                    summary.get("err_title", 0),
                    summary.get("err_short", 0),
                    summary.get("err_long", 0),
                )
                if summary.get("import_error"):
                    line += "\nImport error: %s" % summary.get("import_error")

                self.ctrl._call_ui(lambda h=head, l=line: self._set_status(h + l))

                # Force-save ON-cache to disk periodically (and after each service when manual translate)
                try:
                    self.ctrl._save_epg_cache(force=True)
                except Exception:
                    pass

            self._done = True
        except Exception:
            self._done = True

        try:
            self.ctrl._net_override = _old_net_ov
        except Exception:
            pass

        self.ctrl._call_ui(lambda: self._finish(totals))

    def _finish(self, totals):
        try:
            msg = []
            msg.append("Finished translating bouquet.")
            msg.append("Services processed: %s" % totals.get("services", 0))
            msg.append("Total events: %s" % totals.get("total", 0))
            msg.append("Translated: %s" % totals.get("translated", 0))
            msg.append("Imported: %s" % totals.get("imported", 0))
            msg.append("Errors T/S/L: %s/%s/%s" % (totals.get("err_title", 0), totals.get("err_short", 0), totals.get("err_long", 0)))
            _t = 10
            try:
                _t = int(config.plugins.aifury.done_notify_timeout.value)
            except Exception:
                _t = 10
            if _t <= 0:
                self.session.open(MessageBox, "\n".join(msg), MessageBox.TYPE_INFO)
            else:
                self.session.open(MessageBox, "\n".join(msg), MessageBox.TYPE_INFO, timeout=_t)
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass


class AIFuryInfoScreen(Screen):
    skin = (
        '<screen name="AIFuryInfoScreen" position="center,center" size="700,400" title="AIFury Info">'
        '  <widget name="logo" position="20,20" size="150,150" alphatest="on" />'
        '  <widget name="text" position="190,20" size="490,320" font="Regular;20" halign="left" valign="top" />'
        '  <eLabel text="OK / EXIT = Close" position="20,360" size="660,20" font="Regular;18" halign="center" />'
        '</screen>'
    )

    def __init__(self, session):
        Screen.__init__(self, session)
        self.session = session
        self["logo"] = Pixmap()
        self["text"] = Label("")

        # INFO key (and Blue key from setup) opens this screen
        self["actions"] = ActionMap(
            ["OkCancelActions", "InfoActions", "InfobarActions", "EPGSelectActions"],
            {
                "ok": self.close,
                "cancel": self.close,
                "info": self.close,
                "showEventInfo": self.close,
                "displayHelp": self.close,
                "help": self.close,
            },
            -1,
        )

        txt = []
        try:
            txt.append("AIFury - EPG Translation Stats")
            txt.append("")
        except Exception:
            pass

        ctrl = None
        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None

        # Current EPG translation languages
        try:
            title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            title_lang = ""
        try:
            descr_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            descr_lang = ""
        if not descr_lang:
            descr_lang = title_lang

        if not (title_lang or descr_lang):
            txt.append("EPG translation language: Disabled")
        else:
            txt.append("EPG title language : %s" % (title_lang or ""))
            txt.append("EPG descr language : %s" % (descr_lang or ""))
            if ctrl:
                try:
                    total = ctrl.get_epg_cache_count(title_lang, descr_lang)
                except Exception:
                    total = 0
                txt.append("")
                txt.append("Translated events in cache: %d" % int(total))
            else:
                txt.append("")
                txt.append("Translated events in cache: (controller not ready)")

        # Bouquet (package) info
        try:
            bref = (config.plugins.aifury.last_bouquet_ref.value or "").strip()
        except Exception:
            bref = ""
        if bref:
            try:
                bname = _bouquet_name_from_ref(bref) or ""
            except Exception:
                bname = ""
            txt.append("")
            txt.append("Bouquet (package): %s" % (bname or "Unknown"))
            if ctrl and (title_lang or descr_lang):
                try:
                    srefs = _list_services_in_bouquet(bref)
                except Exception:
                    srefs = []
                try:
                    bcount = ctrl.get_epg_cache_count(title_lang, descr_lang, service_refs=srefs)
                except Exception:
                    bcount = 0
                txt.append("Translated events in bouquet: %d" % int(bcount))
                txt.append("Bouquet services: %d" % (len(srefs) if srefs else 0))
        else:
            txt.append("")
            txt.append("Bouquet (package): Not selected yet")

        # Background translation info
        try:
            bg_enabled = bool(getattr(config.plugins.aifury, "auto_translate_epg", None).value)
        except Exception:
            bg_enabled = False

        txt.append("")
        txt.append("Auto translate EPG in background: %s" % ("Enabled" if bg_enabled else "Disabled"))

        if bg_enabled and ctrl:
            st = ctrl.get_bg_stats()
            try:
                txt.append("Background runs: %d" % int(st.get("runs", 0) or 0))
            except Exception:
                pass
            try:
                txt.append("Translated in background: %d" % int(st.get("translated", 0) or 0))
            except Exception:
                pass
            try:
                txt.append("Imported in background: %d" % int(st.get("imported", 0) or 0))
            except Exception:
                pass
            try:
                lts = int(st.get("last_ts", 0) or 0)
            except Exception:
                lts = 0
            if lts:
                try:
                    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(lts))
                except Exception:
                    when = str(lts)
                txt.append("Last background run: %s" % when)
            try:
                lsrv = str(st.get("last_service", "") or "")
            except Exception:
                lsrv = ""
            if lsrv:
                txt.append("Last service: %s" % lsrv)

        try:
            self["text"].setText("\n".join(txt))
        except Exception:
            pass



# ---------- custom language screen ----------

class AIFuryLanguageScreen(Screen):
    """
    شاشة خاصة لاختيار لغة الترجمة.
    نستخدم قائمة ثابتة AIFury_LANG_CHOICES ونعرض أسماء اللغات فقط.
    """
    skin = (
        '<screen name="AIFuryLanguageScreen" position="center,center" size="600,400" title="Select translation language">'
        '  <widget name="list" position="20,20" size="560,320" font="Regular;22" itemHeight="32" scrollbarMode="showOnDemand" />'
        '  <eLabel text="OK = Select   |   EXIT = Cancel" position="20,355" size="560,25" font="Regular;18" halign="center" />'
        '</screen>'
    )

    def __init__(self, session, current_value=None, include_disabled=False):
        Screen.__init__(self, session)
        self.session = session

        # (code, label) من الثابت
        self.lang_choices = AIFury_LANG_CHOICES[:]  # copy

        if include_disabled:
            self.lang_choices = [("", "Disabled")] + self.lang_choices
        labels = [label for (code, label) in self.lang_choices]
        self.code_by_label = {}
        for code, label in self.lang_choices:
            self.code_by_label[label] = code

        self["list"] = MenuList(labels)

        # حرك المؤشر على اللغة الحالية
        try:
            current_label = None
            for code, label in self.lang_choices:
                if code == current_value:
                    current_label = label
                    break
            if current_label is not None and current_label in labels:
                self["list"].moveToIndex(labels.index(current_label))
        except Exception as e:
            print("[AIFury] language screen move index error:", e)

        # إصلاح الـ ActionMap:
        # - OK يستدعي keyOk لاختيار اللغة.
        # - EXIT يستدعي keyCancel لإغلاق الشاشة بدون اختيار.
        self["actions"] = ActionMap(
            ["OkCancelActions"],
            {
                "ok": self.keyOk,
                "cancel": self.keyCancel,
            },
            -1,
        )

    def keyOk(self):
        try:
            label = self["list"].getCurrent()
        except Exception:
            label = None

        if not label:
            self.close(None)
            return

        # في بعض نسخ MenuList قد ترجع (label,) بدل string
        if isinstance(label, (tuple, list)) and label:
            label = label[0]

        code = self.code_by_label.get(label)
        self.close(code)

    def keyCancel(self):
        # إغلاق بدون تغيير اللغة
        self.close(None)


# ---------- setup screen ----------


def _aifury__child_dict(node):
    """Return child dict for ConfigSubsection across different images."""
    try:
        d = getattr(node, "dict", None)
        if callable(d):
            d = d()
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    try:
        d = getattr(node, "__dict__", None)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}

def _aifury__iter_config_elements(node, _seen=None):
    if _seen is None:
        _seen = set()
    try:
        nid = id(node)
        if nid in _seen:
            return
        _seen.add(nid)
    except Exception:
        pass

    d = _aifury__child_dict(node)
    for k, v in d.items():
        if v is None:
            continue
        # Config elements usually have: save(), default, setValue()
        if hasattr(v, "save") and hasattr(v, "default") and hasattr(v, "setValue"):
            yield v
        else:
            # Recurse into subsections/containers
            if hasattr(v, "dict") or hasattr(v, "__dict__"):
                for x in _aifury__iter_config_elements(v, _seen=_seen):
                    yield x

def aifury_factory_reset_defaults():
    """Reset ONLY AIFury plugin config to defaults and persist."""
    try:
        root = config.plugins.aifury
    except Exception:
        return False

    ok = True
    for elem in _aifury__iter_config_elements(root):
        try:
            elem.setValue(elem.default)
            elem.save()
        except Exception:
            ok = False

    try:
        config.plugins.aifury.save()
    except Exception:
        pass
    try:
        configfile.save()
    except Exception:
        pass
    return ok

def aifury_clear_caches():
    """Remove AIFury cache files (best-effort)."""
    paths = []

    def _add_path(p):
        try:
            if p and isinstance(p, str):
                p = p.strip()
                if p and p.lower() != "no path" and p not in paths:
                    paths.append(p)
        except Exception:
            pass

    try:
        _add_path(getattr(config.plugins.aifury.cachepath, "value", ""))
    except Exception:
        pass
    try:
        _add_path(getattr(config.plugins.aifury.epgcachepath, "value", ""))
    except Exception:
        pass

    # Also try common legacy names in the same directory
    try:
        base_dir = None
        if paths:
            base_dir = os.path.dirname(paths[0])
        if not base_dir:
            base_dir = "/tmp"
        for name in [
            "aifury-cache",
            "aifury-epg-cache",
            "aifury_cache.json",
            "aifury_epg_cache.json",
            "aifury_epg_cache",
            "aifury_cache",
        ]:
            _add_path(os.path.join(base_dir, name))
    except Exception:
        pass

    removed = 0
    for p in paths:
        try:
            if os.path.isfile(p):
                os.remove(p)
                removed += 1
        except Exception:
            pass
    return removed

def aifury_restart_gui(session):
    """Restart Enigma2 GUI only."""
    try:
        from Screens.Standby import TryQuitMainloop
        session.open(TryQuitMainloop, 3)
        return True
    except Exception:
        pass
    try:
        os.system("killall -9 enigma2")
        return True
    except Exception:
        return False


class AIFurySetup(Screen, ConfigListScreen):
    skin = (
        '<screen name="AIFurySetup" position="center,center" size="800,400" title="AIFury Setup">'
        '<widget name="config" position="20,20" size="760,300" scrollbarMode="showOnDemand" />'
        '<widget name="info" position="20,330" size="760,40" font="Regular;20" halign="center" valign="center" />'
        '<eLabel text="OK = Choose option   |   Green = Save   |   Yellow = Clear cache   |   Blue = Stats   |   Red/EXIT = Cancel" '
        'position="20,370" size="760,20" font="Regular;18" halign="center" />'
        '</screen>'
    )

    def __init__(self, session):
        Screen.__init__(self, session)
        self.session = session

        self["info"] = Label("AIFury - translation control")

        # Build dynamic setup list (options appear/disappear based on "Enable Translate Current Event")
        self._cfg_translate_current_epg = ConfigNothing()
        self._cfg_translate_choose_bouquet = ConfigNothing()
        self._cfg_translate_choose_bouquet_bg = ConfigNothing()

        # Bouquet selection is done on-demand via ChoiceBox (no ConfigSelection here).
        lst = self._getConfigList()

        ConfigListScreen.__init__(self, lst, session=session)

        # Deferred rebuild timer (avoids re-entrancy crashes on some images)
        self._pending_restore_original = False
        self._restore_service_ref = None
        self._rebuild_timer = eTimer()
        try:
            self._rebuild_timer.callback.append(self._onRebuildTimer)
        except Exception:
            try:
                self._rebuild_timer.timeout.get().append(self._onRebuildTimer)
            except Exception:
                pass

        # Refresh setup list when "Enable Translate Current Event" is toggled
        try:
            config.plugins.aifury.enable_translate_current_event.addNotifier(self._translateCurrentEventToggled, initial_call=False)
        except Exception:
            pass

        # Refresh setup list when "Enable Translate" is toggled
        try:
            config.plugins.aifury.enabled.addNotifier(self._translateEnabledToggled, initial_call=False)
        except Exception:
            pass

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "InfoActions", "InfobarActions", "EPGSelectActions"],
            {
                "ok": self.keyOk,
                "green": self.keySave,
                "yellow": self.keyClear,
                "blue": self.keyStats,
                "info": self.keyInfo,
                "showEventInfo": self.keyInfo,
                "displayHelp": self.keyStats,
                "help": self.keyStats,
                "red": self.keyCancel,
                "cancel": self.keyCancel,
            },
            -1,
        )


    def _getConfigList(self):
        """Return ConfigList entries (dynamic)."""
        lst = []
        # Master switch: when disabled, hide all other options
        lst.append(getConfigListEntry("Enable Translate", config.plugins.aifury.enabled))

        if not config.plugins.aifury.enabled.value:
            return lst

        lst.append(getConfigListEntry("Translation language", config.plugins.aifury.language))
        lst.append(getConfigListEntry("Enable Translate Current Event", config.plugins.aifury.enable_translate_current_event))

        # These options are only relevant when Translate Current Event is enabled
        if config.plugins.aifury.enable_translate_current_event.value:
            # Safety: some images call notifiers on partially-initialized/old instances
            # Ensure the dummy Config entry exists before building the list.
            try:
                if (not hasattr(self, "_cfg_translate_current_epg")) or (self._cfg_translate_current_epg is None):
                    self._cfg_translate_current_epg = ConfigNothing()
            except Exception:
                try:
                    self._cfg_translate_current_epg = ConfigNothing()
                except Exception:
                    self._cfg_translate_current_epg = None
            lst.append(getConfigListEntry("Translate Current Event", self._cfg_translate_current_epg))
            try:
                if (not hasattr(self, "_cfg_translate_choose_bouquet")) or (self._cfg_translate_choose_bouquet is None):
                    self._cfg_translate_choose_bouquet = ConfigNothing()
            except Exception:
                try:
                    self._cfg_translate_choose_bouquet = ConfigNothing()
                except Exception:
                    self._cfg_translate_choose_bouquet = None
            lst.append(getConfigListEntry("Translate bouquet (choose from list)", self._cfg_translate_choose_bouquet))
            # Background bouquet translation (non-blocking)
            try:
                if (not hasattr(self, "_cfg_translate_choose_bouquet_bg")) or (self._cfg_translate_choose_bouquet_bg is None):
                    self._cfg_translate_choose_bouquet_bg = ConfigNothing()
            except Exception:
                try:
                    self._cfg_translate_choose_bouquet_bg = ConfigNothing()
                except Exception:
                    self._cfg_translate_choose_bouquet_bg = None
            lst.append(getConfigListEntry("Translate bouquet in background", self._cfg_translate_choose_bouquet_bg))
            lst.append(getConfigListEntry("Notify translation end", config.plugins.aifury.bouquet_bg_notify_done))
            lst.append(getConfigListEntry("Done notification timeout", config.plugins.aifury.done_notify_timeout))
            lst.append(getConfigListEntry("EPG title language", config.plugins.aifury.epg_title_lang))
            lst.append(getConfigListEntry("Auto restore translated EPG on zap", config.plugins.aifury.auto_restore_epg))

        if config.plugins.aifury.auto_restore_epg.value:
            lst.append(getConfigListEntry("Periodic restore interval", config.plugins.aifury.keep_restore_interval))
            lst.append(getConfigListEntry("Auto translate EPG in background", config.plugins.aifury.auto_translate_epg))

        lst.append(getConfigListEntry("Auto translate horizon", config.plugins.aifury.auto_translate_horizon))
        lst.append(getConfigListEntry("Auto translate min gap (minutes)", config.plugins.aifury.auto_translate_min_gap))
        lst.append(getConfigListEntry("Auto translate max events", config.plugins.aifury.auto_translate_max_events))
        lst.append(getConfigListEntry("Main path", config.plugins.aifury.cachepath))
        lst.append(getConfigListEntry("EPG path", config.plugins.aifury.epgcachepath))
        lst.append(getConfigListEntry("Network timeout (sec)", config.plugins.aifury.req_timeout))
       # lst.append(getConfigListEntry("Retry count", config.plugins.aifury.req_retries))
        lst.append(getConfigListEntry("Min request interval (ms)", config.plugins.aifury.min_interval_ms))
        lst.append(getConfigListEntry("Worker threads", config.plugins.aifury.workers))

        lst.append(getConfigListEntry("Reset: Defaults + Caches", config.plugins.aifury.maint_reset_all))
        lst.append(getConfigListEntry("Reset: Defaults only", config.plugins.aifury.maint_reset_defaults))
        lst.append(getConfigListEntry("Clear caches only", config.plugins.aifury.maint_clear_caches))
        return lst

    def _rebuildConfigList(self):
        """Rebuild list in-place to show/hide dependent options immediately."""
        # If screen is being closed/disposed, skip
        try:
            if not self.instance:
                return
        except Exception:
            pass
        try:
            idx = self["config"].getCurrentIndex()
            if idx is None:
                idx = 0
        except Exception:
            idx = 0

        new_list = self._getConfigList()

        # Update underlying list widget (compat across images)
        try:
            self["config"].list = new_list
            self["config"].l.setList(new_list)
        except Exception:
            try:
                self["config"].setList(new_list)
            except Exception:
                pass

        if idx >= len(new_list):
            idx = max(0, len(new_list) - 1)

        try:
            self["config"].setCurrentIndex(idx)
        except Exception:
            try:
                self["config"].moveToIndex(idx)
            except Exception:
                pass


    def _onRebuildTimer(self):
        # Defer rebuild and optional restore to avoid re-entrancy crashes
        try:
            self._rebuildConfigList()
        except Exception:
            pass
        try:
            if getattr(self, "_pending_restore_original", False):
                self._pending_restore_original = False
                self._restore_original_epg()
        except Exception:
            pass

    def _restore_original_epg(self):
        """Revert any translated EPG back to provider/original language and clear translation cache."""
        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None
        if ctrl is None:
            return

        def _run():
            try:
                ref = getattr(self, "_restore_service_ref", None)
                ctrl.schedule_revert_all_translated_epg_to_original(delay_ms=0)
            except Exception:
                try:
                    ctrl.schedule_revert_all_translated_epg_to_original(delay_ms=0)
                except Exception:
                    pass
            try:
                self._restore_service_ref = None
            except Exception:
                pass

        try:
            ctrl._submit_bg(_run)
        except Exception:
            _run()
    def _translateCurrentEventToggled(self, *args, **kwargs):
        # Called by addNotifier when config.plugins.aifury.enable_translate_current_event changes
        # When disabling the feature, revert translated EPG back to original/provider texts.
        try:
            if not config.plugins.aifury.enable_translate_current_event.value:
                self._pending_restore_original = True
                # Remember current service to restore only that service (faster + more predictable)
                sref = None
                try:
                    sref = self.session.nav.getCurrentlyPlayingServiceReference()
                except Exception:
                    try:
                        sref = self.session.nav.getCurrentServiceReference()
                    except Exception:
                        sref = None
                self._restore_service_ref = sref
            else:
                self._pending_restore_original = False
                self._restore_service_ref = None
                # Feature enabled again: restore previously saved translations from disk cache
                # so the user doesn't need to translate from scratch every time.
                try:
                    ctrl = AIFuryController.getInstance()
                    try:
                        ctrl.schedule_restore_all_epg_from_cache(delay_ms=0)
                    except Exception:
                        pass
                except Exception:
                    pass
                except Exception:
                    pass
        except Exception:
            pass

        # Defer the list rebuild to the next mainloop tick to avoid re-entrancy crashes
        try:
            self._rebuild_timer.stop()
        except Exception:
            pass
        try:
            # eTimer API differs between images: start(ms, singleShot) vs start(ms)
            try:
                self._rebuild_timer.start(0, True)
            except TypeError:
                self._rebuild_timer.start(0)
            return
        except Exception:
            pass
        # Fallback if timer API differs
        try:
            self._rebuild_timer.startLongTimer(0)
            return
        except Exception:
            pass
        self._rebuildConfigList()


    def _translateEnabledToggled(self, *args, **kwargs):
        # Called by addNotifier when config.plugins.aifury.enabled changes
        # When disabling the whole plugin, revert translated EPG back to original/provider texts.
        try:
            if not config.plugins.aifury.enabled.value:
                self._pending_restore_original = True
                sref = None
                try:
                    sref = self.session.nav.getCurrentlyPlayingServiceReference()
                except Exception:
                    try:
                        sref = self.session.nav.getCurrentServiceReference()
                    except Exception:
                        sref = None
                self._restore_service_ref = sref
            else:
                self._pending_restore_original = False
                self._restore_service_ref = None
                # Plugin enabled again: restore saved translations from disk cache (if feature is enabled)
                try:
                    if config.plugins.aifury.enable_translate_current_event.value:
                        ctrl = AIFuryController.getInstance()
                        try:
                            ctrl.schedule_restore_all_epg_from_cache(delay_ms=0)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Defer rebuild (avoids re-entrancy crashes on some images)
        try:
            self._rebuild_timer.stop()
        except Exception:
            pass
        try:
            try:
                self._rebuild_timer.start(0, True)
            except TypeError:
                self._rebuild_timer.start(0)
            return
        except Exception:
            pass
        try:
            self._rebuild_timer.startLongTimer(0)
            return
        except Exception:
            pass
        self._rebuildConfigList()


    def keyOk(self):

        """
        OK:
        - Translation language → شاشة لغات خاصة.
        - باقي ConfigSelection / YesNo → ChoiceBox.
        - Cache file path → اختيار مجلد فقط.
        - منع الكيبورد لأي ConfigText.
        """
        cur = self["config"].getCurrent()
        if cur is None:
            return

        title = cur[0] or ""
        cfg = cur[1]

        # ---------- Action: Translate current channel EPG ----------
        try:
            if hasattr(self, "_cfg_translate_current_epg") and cfg is self._cfg_translate_current_epg:
                if (not hasattr(config.plugins.aifury, "enable_translate_current_event")) or (not config.plugins.aifury.enable_translate_current_event.value):
                    try:
                        self.session.open(MessageBox, "Translate Current Event is disabled in settings.", MessageBox.TYPE_INFO, timeout=4)
                    except Exception:
                        pass
                    return
                self._start_translate_current_epg()
                return

            if hasattr(self, "_cfg_translate_choose_bouquet") and cfg is self._cfg_translate_choose_bouquet:
                if (not hasattr(config.plugins.aifury, "enable_translate_current_event")) or (not config.plugins.aifury.enable_translate_current_event.value):
                    try:
                        self.session.open(MessageBox, "Translate Current Event is disabled in settings.", MessageBox.TYPE_INFO, timeout=4)
                    except Exception:
                        pass
                    return
                self._openBouquetChooser()
                return

            if hasattr(self, "_cfg_translate_choose_bouquet_bg") and cfg is self._cfg_translate_choose_bouquet_bg:
                if (not hasattr(config.plugins.aifury, "enable_translate_current_event")) or (not config.plugins.aifury.enable_translate_current_event.value):
                    try:
                        self.session.open(MessageBox, "Translate Current Event is disabled in settings.", MessageBox.TYPE_INFO, timeout=4)
                    except Exception:
                        pass
                    return
                self._openBouquetChooserBackground()
                return

        except Exception:
            pass

        from Components.config import ConfigSelection, ConfigYesNo, ConfigText

        # ---------- 1) Translation language ----------
        if "Translation language" in title:
            current_value = config.plugins.aifury.language.value

            def _cb_lang(code):
                if not code:
                    return
                try:
                    config.plugins.aifury.language.value = code
                    try:
                        config.plugins.aifury.language.save()
                        config.plugins.aifury.save()
                        configfile.save()
                    except Exception as e:
                        print("[AIFury] error saving language:", e)
                    self["config"].invalidate()
                    print("[AIFury] language set to", code)
                except Exception as e:
                    print("[AIFury] error setting language:", e)

            self.session.openWithCallback(
                _cb_lang,
                AIFuryLanguageScreen,
                current_value,
            )
            return

        
        # ---------- 1b) EPG title/description language ----------
        # بعض الصور/الاسكينات بتعمل كراش مع ChoiceBox لما القائمة فيها Unicode،
        # فبنستخدم نفس شاشة اللغات الخاصة (MenuList) وندعم Disabled.
        if "EPG title language" in title or "EPG description language" in title:
            current_value = ""
            try:
                current_value = cfg.value
            except Exception:
                current_value = ""

            def _cb_epg_lang(code):
                # code ممكن يكون "" (Disabled) أو كود لغة
                if code is None:
                    return
                try:
                    cfg.value = code
                    try:
                        cfg.save()
                        config.plugins.aifury.save()
                        configfile.save()
                    except Exception as e:
                        print("[AIFury] error saving EPG lang:", e)
                    self["config"].invalidate()
                    print("[AIFury] EPG lang set to", code)
                except Exception as e:
                    print("[AIFury] error setting EPG lang:", e)

            self.session.openWithCallback(
                _cb_epg_lang,
                AIFuryLanguageScreen,
                current_value,
                True,  # include Disabled
            )
            return

# ---------- 2) ConfigSelection / YesNo أخرى ----------
        if isinstance(cfg, (ConfigSelection, ConfigYesNo)):
            display_list = []

            if isinstance(cfg, ConfigYesNo):
                display_list = [("Yes", True), ("No", False)]
            else:
                # cfg.choices may vary across images: can be (value, text), value-only, or other forms.
                for ch in getattr(cfg, "choices", []):
                    try:
                        if isinstance(ch, (tuple, list)):
                            if len(ch) >= 2:
                                val, text = ch[0], ch[1]
                            elif len(ch) == 1:
                                val, text = ch[0], str(ch[0])
                            else:
                                val, text = ch, str(ch)
                        else:
                            val, text = ch, str(ch)
                        display_list.append((str(text), val))
                    except Exception:
                        display_list.append((str(ch), ch))

            def _cb_sel(res):
                if res is None:
                    return
                value = res[1]
                try:
                    cfg.value = value
                    self["config"].invalidate()
                except Exception as e:
                    print("[AIFury] error setting selection:", e)

            self.session.openWithCallback(
                _cb_sel,
                ChoiceBox,
                title="Select value",
                list=display_list,
            )
            return

        # ---------- 3) Cache file path ----------
        if cfg == config.plugins.aifury.cachepath:
            choices = []

            mounts = [
                ("/media/hdd", "HDD (/media/hdd)"),
                ("/media/usb", "USB (/media/usb)"),
                ("/media/usb0", "USB0 (/media/usb0)"),
                ("/media/usb1", "USB1 (/media/usb1)"),
                ("/media/mmc", "MMC (/media/mmc)"),
                ("/media/mmc1", "MMC1 (/media/mmc1)"),
            ]
            for path, label in mounts:
                if os.path.isdir(path):
                    choices.append((label, path))

            choices.append(("Internal /tmp (/tmp)", "/tmp"))

            def _cb_cache_dir(res):
                if res is None:
                    return
                base_dir = res[1] or "/tmp"
                try:
                    base_dir = ("%s" % base_dir).strip().rstrip("/") or "/tmp"
                    plugin_dir = os.path.join(base_dir, "AIFury")
                    file_path = os.path.join(plugin_dir, "aifury_cache.json")

                    try:
                        if not os.path.isdir(plugin_dir):
                            os.makedirs(plugin_dir)
                    except Exception as ee:
                        print("[AIFury] warning: could not create cache dir: %s" % ee)

                    cfg.value = file_path
                    self["config"].invalidate()
                    print("[AIFury] new cache path selected (choicebox):", file_path)

                    ctrl = AIFuryController.instance
                    if ctrl is not None:
                        try:
                            ctrl.cachepath = file_path
                            ctrl._load_cache()
                        except Exception as ee:
                            print("[AIFury] error reloading cache after path change: %s" % ee)

                except Exception as e:
                    print("[AIFury] error in cache dir ChoiceBox callback: %s" % e)

            self.session.openWithCallback(
                _cb_cache_dir,
                ChoiceBox,
                title="Select cache directory",
                list=choices,
            )

        # ---------- 3b) EPG translations cache path (Translate Current Event) ----------
        if cfg == getattr(config.plugins.aifury, "epgcachepath", None):
            choices = []

            mounts = [
                ("/media/hdd", "HDD (/media/hdd)"),
                ("/media/usb", "USB (/media/usb)"),
                ("/media/usb0", "USB0 (/media/usb0)"),
                ("/media/usb1", "USB1 (/media/usb1)"),
                ("/media/mmc", "MMC (/media/mmc)"),
                ("/media/mmc1", "MMC1 (/media/mmc1)"),
            ]
            for path, label in mounts:
                if os.path.isdir(path):
                    choices.append((label, path))

            choices.append(("Internal /tmp (/tmp)", "/tmp"))

            def _cb_epg_cache_dir(res):
                if res is None:
                    return
                base_dir = res[1] or "/tmp"
                try:
                    base_dir = ("%s" % base_dir).strip().rstrip("/") or "/tmp"
                    plugin_dir = os.path.join(base_dir, "AIFury")
                    file_path = os.path.join(plugin_dir, "aifury_epg_cache.json")

                    try:
                        if not os.path.isdir(plugin_dir):
                            os.makedirs(plugin_dir)
                    except Exception as ee:
                        print("[AIFury] warning: could not create epg cache dir: %s" % ee)

                    cfg.value = file_path
                    self["config"].invalidate()
                    print("[AIFury] new EPG cache path selected:", file_path)

                    ctrl = AIFuryController.instance
                    if ctrl is not None:
                        try:
                            ctrl.epg_cachepath = file_path
                            ctrl._load_epg_cache()
                        except Exception as ee:
                            print("[AIFury] error reloading epg cache after path change: %s" % ee)

                except Exception as e:
                    print("[AIFury] error in epg cache dir callback: %s" % e)

            self.session.openWithCallback(
                _cb_epg_cache_dir,
                ChoiceBox,
                title="Select EPG translations cache directory",
                list=choices,
            )
            return

            return

        # ---------- 4) منع الكيبورد لأي ConfigText ----------
        if isinstance(cfg, ConfigText):
            return

        # ---------- 5) الافتراضي لأي نوع آخر ----------
        try:
            ConfigListScreen.keyOK(self)
        except Exception as e:
            print("[AIFury] keyOk default handler error: %s" % e)


    def _start_translate_current_epg(self):
        """
        Translate current playing service EPG and import translated events into RAM.
        """
        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None

        if ctrl is None:
            try:
                self.session.open(MessageBox, "AIFury controller is not initialized.", MessageBox.TYPE_ERROR, timeout=5)
            except Exception:
                pass
            return

        title_lang = config.plugins.aifury.epg_title_lang.value
        descr_lang = config.plugins.aifury.epg_title_lang.value
        if not (title_lang or descr_lang):
            try:
                self.session.open(MessageBox, "Select EPG title/description language first.", MessageBox.TYPE_INFO, timeout=5)
            except Exception:
                pass
            return

        try:
            sref = None
            try:
                sref = self.session.nav.getCurrentlyPlayingServiceReference()
            except Exception:
                sref = None
            if sref is None:
                try:
                    sref = self.session.nav.getCurrentServiceReference()
                except Exception:
                    sref = None
        except Exception:
            sref = None

        if sref is None:
            try:
                self.session.open(MessageBox, "No current service is available.", MessageBox.TYPE_ERROR, timeout=5)
            except Exception:
                pass
            return

        try:
            longdesc_days = int("0" or "0")
        except Exception:
            longdesc_days = 0

        try:

            try:

                ctrl._epg_cache_force_save = True

            except Exception:

                pass
            self.session.open(AIFuryEpgTranslateProgress, ctrl, sref, title_lang, descr_lang, longdesc_days)
        except Exception as e:
            try:
                self.session.open(MessageBox, "Cannot start EPG translation: %s" % e, MessageBox.TYPE_ERROR, timeout=6)
            except Exception:
                pass



    def _openBouquetChooser(self):
        """Show available TV bouquets, then translate the selected bouquet."""
        try:
            bouquets = _get_bouquet_choices(tv=True)
        except Exception:
            bouquets = []

        if not bouquets:
            try:
                self.session.open(MessageBox, "No bouquets found.", MessageBox.TYPE_INFO, timeout=4)
            except Exception:
                pass
            return

        clist = []
        for ref, name in bouquets:
            try:
                disp = name or _bouquet_name_from_ref(ref) or ref
            except Exception:
                disp = name or ref
            try:
                if isinstance(disp, bytes):
                    disp = disp.decode("utf-8", "ignore")
            except Exception:
                pass
            clist.append((disp, ref))

        # prefer last chosen bouquet
        try:
            default_ref = (config.plugins.aifury.last_bouquet_ref.value or "").strip()
        except Exception:
            default_ref = ""
        sel = 0
        if default_ref:
            for i, it in enumerate(clist):
                try:
                    if it[1] == default_ref:
                        sel = i
                        break
                except Exception:
                    pass

        try:
            self.session.openWithCallback(self._onBouquetChosen, ChoiceBox, title="Select bouquet to translate", list=clist, selection=sel)
        except Exception:
            try:
                self.session.openWithCallback(self._onBouquetChosen, ChoiceBox, title="Select bouquet to translate", list=clist)
            except Exception:
                pass


    def _openBouquetChooserBackground(self):
        """Show available TV bouquets, then start translating the selected bouquet in background."""
        try:
            bouquets = _get_bouquet_choices(tv=True)
        except Exception:
            bouquets = []

        if not bouquets:
            try:
                self.session.open(MessageBox, "No bouquets found.", MessageBox.TYPE_INFO, timeout=4)
            except Exception:
                pass
            return

        clist = []
        for ref, name in bouquets:
            try:
                disp = name or _bouquet_name_from_ref(ref) or ref
            except Exception:
                disp = name or ref
            try:
                if isinstance(disp, bytes):
                    disp = disp.decode("utf-8", "ignore")
            except Exception:
                pass
            clist.append((disp, ref))

        # prefer last chosen bouquet
        try:
            default_ref = (config.plugins.aifury.last_bouquet_ref.value or "").strip()
        except Exception:
            default_ref = ""
        sel = 0
        if default_ref:
            for i, it in enumerate(clist):
                try:
                    if it[1] == default_ref:
                        sel = i
                        break
                except Exception:
                    pass

        try:
            self.session.openWithCallback(
                self._onBouquetChosenBg,
                ChoiceBox,
                title="Select bouquet to translate (background)",
                list=clist,
                selection=sel,
            )
        except Exception:
            try:
                self.session.openWithCallback(
                    self._onBouquetChosenBg, ChoiceBox, title="Select bouquet to translate (background)", list=clist
                )
            except Exception:
                pass

    def _onBouquetChosenBg(self, choice):
        """ChoiceBox callback for background bouquet translation."""
        try:
            ref = choice[1] if isinstance(choice, (list, tuple)) and len(choice) > 1 else choice
        except Exception:
            ref = ""
        if not ref:
            return
        try:
            config.plugins.aifury.last_bouquet_ref.value = str(ref)
            config.plugins.aifury.last_bouquet_ref.save()
        except Exception:
            pass
        self._start_translate_bouquet_background(ref)

    def _notify_popup(self, text, timeout=8, msgtype=None):
        """Non-blocking notification when supported; fallback to MessageBox with timeout."""
        if msgtype is None:
            try:
                msgtype = MessageBox.TYPE_INFO
            except Exception:
                msgtype = 0

        # Prefer global notifications (non-modal)
        try:
            from Tools import Notifications  # type: ignore

            try:
                Notifications.AddPopup(text, msgtype, timeout, "AIFury")
                return
            except Exception:
                pass
            try:
                Notifications.AddNotification(MessageBox, text, msgtype, timeout=timeout)
                return
            except Exception:
                pass
        except Exception:
            pass

        try:
            self.session.open(MessageBox, text, msgtype, timeout=timeout)
        except Exception:
            pass

    def _start_translate_bouquet_background(self, bouq_ref):
        """
        Start translating the selected bouquet in background and notify when finished.
        This uses a controller-managed job (timer + thread pool) to mimic Translate Bouquet behavior.
        """
        bouq_ref = (bouq_ref or "").strip()
        if not bouq_ref:
            self._notify_popup("Bouquet is empty/invalid.", timeout=4)
            return

        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None
        if ctrl is None:
            self._notify_popup("AIFury controller not ready.", timeout=4)
            return

        # Prevent concurrent runs
        try:
            if getattr(ctrl, "_bouquet_job_running", False):
                try:
                    bn = getattr(ctrl, "_bouquet_job_name", "") or "bouquet"
                except Exception:
                    bn = "bouquet"
                self._notify_popup("Bouquet translation already running in background: %s" % bn, timeout=6)
                return
        except Exception:
            pass

        # Ensure controller uses current cache paths
        try:
            cfg_raw = (config.plugins.aifury.cachepath.value or "").strip()
            cfg_path = "" if cfg_raw.lower() == "no path" else cfg_raw
            if not cfg_path:
                cfg_path = os.path.join("/tmp", "AIFury", "aifury_cache.json")
            ctrl.cachepath = cfg_path
        except Exception:
            pass
        try:
            epg_raw = (config.plugins.aifury.epgcachepath.value or "").strip()
            epg_path = "" if epg_raw.lower() == "no path" else epg_raw
            if not epg_path:
                epg_path = os.path.join("/tmp", "AIFury", "aifury_epg_cache.json")
            ctrl.epg_cachepath = epg_path
        except Exception:
            pass

        # Resolve languages (fallback to global plugin language if both disabled)
        try:
            title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            title_lang = ""
        try:
            descr_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            descr_lang = ""
        if not title_lang and not descr_lang:
            try:
                fallback_lang = (config.plugins.aifury.language.value or "").strip()
            except Exception:
                fallback_lang = ""
            if fallback_lang:
                title_lang = fallback_lang
                descr_lang = fallback_lang

        try:
            longdesc_days = int("0" or "0")
        except Exception:
            longdesc_days = 0

        # Start controller-managed job
        try:
            ok = ctrl.start_bouquet_bg_job(bouq_ref, title_lang, descr_lang, longdesc_days)
        except Exception:
            ok = False

        if not ok:
            self._notify_popup("Failed to start background bouquet translation.", timeout=6)

    def _onBouquetChosen(self, choice):
        if not choice:
            return
        try:
            ref = choice[1] if isinstance(choice, (tuple, list)) and len(choice) > 1 else choice
        except Exception:
            ref = ""
        if not ref:
            return
        try:
            config.plugins.aifury.last_bouquet_ref.value = str(ref)
            config.plugins.aifury.last_bouquet_ref.save()
        except Exception:
            pass
        self._start_translate_bouquet(ref)

    def _start_translate_bouquet(self, bouq_ref):
        """
        Translate EPG for all services in the given bouquet and persist translations into the ON-cache.
        Uses the user's Auto-translate horizon/max-events as limits (to avoid heavy loads).
        """
        bouq_ref = (bouq_ref or "").strip()
        if not bouq_ref:
            try:
                self.session.open(MessageBox, "Bouquet is empty/invalid.", MessageBox.TYPE_INFO, timeout=4)
            except Exception:
                pass
            return

        # Remember last bouquet
        try:
            if hasattr(config.plugins.aifury, "last_bouquet_ref"):
                config.plugins.aifury.last_bouquet_ref.value = bouq_ref
                config.plugins.aifury.last_bouquet_ref.save()
                config.plugins.aifury.save()
                configfile.save()
        except Exception:
            pass

        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None

        if ctrl is None:
            try:
                self.session.open(MessageBox, "AIFury controller is not initialized.", MessageBox.TYPE_ERROR, timeout=5)
            except Exception:
                pass
            return

        try:
            title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            title_lang = ""
        try:
            descr_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            descr_lang = ""

        services = _list_services_in_bouquet(bouq_ref) or []
        if not services:
            try:
                bn = _bouquet_name_from_ref(bouq_ref) or "Selected bouquet"
                self.session.open(MessageBox, "No playable services found in: %s" % bn, MessageBox.TYPE_INFO, timeout=5)
            except Exception:
                pass
            return

        # Force-save EPG ON-cache after run so file appears immediately
        try:
            ctrl._epg_cache_force_save = True
        except Exception:
            pass

        try:
            longdesc_days = int("0" or "0")
        except Exception:
            longdesc_days = 0

        scr = self.session.open(
            AIFuryBouquetTranslateProgress,
            ctrl,
            services,
            title_lang,
            descr_lang,
            longdesc_days,
            bouq_ref,
        )
        try:
            bn = _bouquet_name_from_ref(bouq_ref) or ""
            if bn:
                scr.setTitle("Translate Bouquet: %s" % bn)
        except Exception:
            pass


    def _start_translate_favourites_bouquet(self):
        """
        Translate EPG for all services in the Favourites bouquet and persist translations into the ON-cache.
        Uses the user's Auto-translate horizon/max-events as limits (to avoid very heavy loads).
        """
        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None

        if ctrl is None:
            try:
                self.session.open(MessageBox, "AIFury controller is not initialized.", MessageBox.TYPE_ERROR, timeout=5)
            except Exception:
                pass
            return

        title_lang = (config.plugins.aifury.epg_title_lang.value or "").strip()
        descr_lang = title_lang
        if not (title_lang or descr_lang):
            try:
                self.session.open(MessageBox, "Select EPG title/description language first.", MessageBox.TYPE_INFO, timeout=5)
            except Exception:
                pass
            return

        # Make sure controller paths follow current config (cache + ON-cache)
        try:
            cfg_raw = (config.plugins.aifury.cachepath.value or "").strip()
            if cfg_raw.lower() == "no path":
                cfg_path = ""
            else:
                cfg_path = cfg_raw
            if not cfg_path:
                cfg_path = os.path.join("/tmp", "AIFury", "aifury_cache.json")
            ctrl.cachepath = cfg_path
            ctrl._load_cache()
        except Exception:
            pass

        try:
            cfg2_raw = (getattr(config.plugins.aifury, "epgcachepath", None).value if hasattr(config.plugins.aifury, "epgcachepath") else "") or ""
            cfg2_raw = (cfg2_raw or "").strip()
            if cfg2_raw.lower() == "no path":
                cfg2_path = ""
            else:
                cfg2_path = cfg2_raw
            if not cfg2_path:
                base_dir = os.path.dirname(getattr(ctrl, "cachepath", "")) or "/tmp"
                cfg2_path = os.path.join(base_dir, "aifury_epg_on_cache")
            ctrl.epg_cachepath = cfg2_path
            ctrl._load_epg_cache()
        except Exception:
            pass

        # Find favourites bouquet and list its services
        bouq_ref = _find_favourites_bouquet_ref(tv=True)
        services = _list_services_in_bouquet(bouq_ref)
        if not services:
            try:
                self.session.open(MessageBox, "No services found in Favourites bouquet.", MessageBox.TYPE_INFO, timeout=6)
            except Exception:
                pass
            return

        try:
            ctrl._epg_cache_force_save = True
        except Exception:
            pass

        try:
            longdesc_days = int("0" or "0")
        except Exception:
            longdesc_days = 0

        self.session.open(AIFuryBouquetTranslateProgress, ctrl, services, title_lang, descr_lang, longdesc_days, bouq_ref)


    def keySave(self):
        # Maintenance actions (triggered on Save)
        try:
            do_reset_all = bool(config.plugins.aifury.maint_reset_all.value)
            do_reset_defaults = bool(config.plugins.aifury.maint_reset_defaults.value)
            do_clear_caches = bool(config.plugins.aifury.maint_clear_caches.value)
        except Exception:
            do_reset_all = do_reset_defaults = do_clear_caches = False

        if do_reset_all or do_reset_defaults or do_clear_caches:
            # Normalize flags: reset_all implies defaults + caches
            if do_reset_all:
                do_reset_defaults = True
                do_clear_caches = True

            self._pending_maintenance = (do_reset_defaults, do_clear_caches)

            parts = []
            if do_reset_defaults:
                parts.append("Reset AIFury settings to defaults")
            if do_clear_caches:
                parts.append("Clear AIFury caches")
            parts.append("Restart GUI (Enigma2)")

            msg = "This will:\n- " + "\n- ".join(parts) + "\n\nContinue?"
            self.session.openWithCallback(
                self._onMaintenanceConfirm,
                MessageBox,
                msg,
                MessageBox.TYPE_YESNO,
                timeout=0,
            )
            return

        for x in self["config"].list:
            x[1].save()
        config.plugins.aifury.save()
        try:
            configfile.save()
        except Exception as e:
            print("[AIFury] error saving configfile:", e)

        ctrl = AIFuryController.instance
        if ctrl is not None:
            ctrl.enabled = config.plugins.aifury.enabled.value
            # apply periodic keep-restore interval
            try:
                ctrl._keep_restore_interval_ms = ctrl._get_keep_restore_interval_ms(default_sec=90)
            except Exception:
                try:
                    ctrl._keep_restore_interval_ms = int(int(config.plugins.aifury.keep_restore_interval.value or "0") * 1000)
                except Exception:
                    ctrl._keep_restore_interval_ms = 0

            # restart/stop keep-restore timer according to the new interval
            try:
                if getattr(ctrl, "_keep_restore_timer", None) is not None:
                    try:
                        ctrl._keep_restore_timer.stop()
                    except Exception:
                        pass
                    if int(getattr(ctrl, "_keep_restore_interval_ms", 0) or 0) > 0 and config.plugins.aifury.auto_restore_epg.value:
                        ref = None
                        try:
                            ref = ctrl.session.nav.getCurrentlyPlayingServiceReference()
                        except Exception:
                            ref = None
                        # Force a restore soon, then periodic keep-restore will maintain it.
                        try:
                            if ref is not None:
                                ctrl.schedule_epg_restore(ref, delay_ms=0)
                        except Exception:
                            pass
                        try:
                            ctrl._timer_start_compat(ctrl._keep_restore_timer, int(getattr(ctrl, "_keep_restore_interval_ms", 0) or 0))
                        except Exception:
                            pass
            except Exception:
                pass

            # apply tuning settings
            try:
                ctrl._min_interval = float(int(config.plugins.aifury.min_interval_ms.value or "0")) / 1000.0
            except Exception:
                ctrl._min_interval = 0.0
            try:
                ctrl._ensure_pool()
            except Exception:
                pass

            cfg_raw = (config.plugins.aifury.cachepath.value or "").strip()
            if cfg_raw.lower() == "no path":
                new_path = os.path.join("/tmp", "AIFury", "aifury_cache.json")
            else:
                new_path = cfg_raw or os.path.join("/tmp", "AIFury", "aifury_cache.json")

            ctrl.cachepath = new_path

            # apply EPG translations cache path (Translate Current Event)
            try:
                cfg2_raw = (getattr(config.plugins.aifury, "epgcachepath", None).value if hasattr(config.plugins.aifury, "epgcachepath") else "") or ""
                cfg2_raw = (cfg2_raw or "").strip()
                if cfg2_raw.lower() == "no path":
                    epg_path = ""
                else:
                    epg_path = cfg2_raw

                if not epg_path:
                    base_dir = os.path.dirname(ctrl.cachepath) or "/tmp"
                    epg_path = os.path.join(base_dir, "aifury_epg_cache.json")

                ctrl.epg_cachepath = epg_path
                ctrl._load_epg_cache()

                # restore EPG for current service (use saved translations if available)
                try:
                    ctrl.schedule_epg_restore(ctrl.session.nav.getCurrentlyPlayingServiceReference(), delay_ms=0)
                    try:
                        ctrl.schedule_auto_translate(ctrl.session.nav.getCurrentlyPlayingServiceReference(), delay_ms=500, force=True)
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception as ee:
                print("[AIFury] error applying EPG cache path: %s" % ee)


            try:
                ctrl._load_cache()
            except Exception:
                pass
        msg = "Settings saved."
        def _after_msg(result=None):
            self.close()

        self.session.openWithCallback(
            _after_msg,
            MessageBox,
            msg,
            MessageBox.TYPE_INFO,
            timeout=3,
        )




    def _onMaintenanceConfirm(self, confirmed):
        try:
            do_reset_defaults, do_clear_caches = getattr(self, "_pending_maintenance", (False, False))
        except Exception:
            do_reset_defaults, do_clear_caches = (False, False)

        # Always clear triggers so it does not repeat
        try:
            config.plugins.aifury.maint_reset_all.setValue(False)
            config.plugins.aifury.maint_reset_defaults.setValue(False)
            config.plugins.aifury.maint_clear_caches.setValue(False)
            config.plugins.aifury.maint_reset_all.save()
            config.plugins.aifury.maint_reset_defaults.save()
            config.plugins.aifury.maint_clear_caches.save()
        except Exception:
            pass

        if not confirmed:
            try:
                self._rebuildConfigList()
            except Exception:
                pass
            return

        # Persist current screen values first (important for caches-only mode)
        try:
            for x in self["config"].list:
                x[1].save()
            config.plugins.aifury.save()
            try:
                configfile.save()
            except Exception:
                pass
        except Exception:
            pass

        if do_reset_defaults:
            try:
                aifury_factory_reset_defaults()
            except Exception:
                pass

        if do_clear_caches:
            try:
                aifury_clear_caches()
            except Exception:
                pass

        # Restart GUI only
        try:
            aifury_restart_gui(self.session)
        except Exception:
            pass
    def keyClear(self):
        ctrl = AIFuryController.instance
        if ctrl is None:
            self.session.open(
                MessageBox,
                "Controller not ready, cannot clear cache.",
                MessageBox.TYPE_ERROR,
                timeout=3,
            )
            return

        choices = [
            ("Clear aifury_cache (main cache)", "main"),
            ("Clear aifury_epg_cache (EPG translations cache)", "epg"),
            ("Clear both caches", "both"),
        ]

        def _cb_clear(res):
            if res is None:
                return
            action = res[1]
            cleared = []
            try:
                # Main translation cache
                if action in ("main", "both"):
                    try:
                        ctrl.clear_cache()
                        cleared.append("aifury_cache")
                    except Exception as e:
                        print("[AIFury] keyClear: error clearing main cache: %s" % e)

                # Persistent EPG translation cache (Translate Current Event)
                if action in ("epg", "both"):
                    try:
                        ctrl.clear_epg_translation_cache()
                        cleared.append("aifury_epg_cache")
                    except Exception as e:
                        print("[AIFury] keyClear: error clearing EPG cache: %s" % e)
                msg = "Cache cleared successfully."
                if cleared:
                    msg += "\n(" + " , ".join(cleared) + ")"
                self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=4)

            except Exception as e:
                print("[AIFury] keyClear error: %s" % e)
                self.session.open(
                    MessageBox,
                    "Error clearing cache:\n%s" % e,
                    MessageBox.TYPE_ERROR,
                    timeout=4,
                )

        try:
            self.session.openWithCallback(
                _cb_clear,
                ChoiceBox,
                title="Select cache to clear",
                list=choices,
            )
        except Exception:
            # Fallback: clear both if ChoiceBox fails
            _cb_clear(("Clear both caches", "both"))
    def _build_stats_message(self):
        ctrl = None
        try:
            ctrl = AIFuryController.instance
        except Exception:
            ctrl = None

        # Read configured languages (keep exact user config; do NOT auto-fill descr from title)
        try:
            title_code = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            title_code = ""
        try:
            descr_code = (config.plugins.aifury.epg_title_lang.value or "").strip()
        except Exception:
            descr_code = ""

        # Map language code -> label (e.g. "ar" -> "Arabic")
        try:
            lang_map = dict(AIFury_LANG_CHOICES)
        except Exception:
            lang_map = {}

        # What to display as "EPG Title Language"
        if title_code:
            title_label = lang_map.get(title_code, title_code)
        elif descr_code:
            # If title is disabled but description is enabled, show description language
            title_label = lang_map.get(descr_code, descr_code)
        else:
            title_label = "Disabled"

        def _count_epg_cache(ctrl_obj, tcode, dcode, service_refs=None):
            """Count unique cached translated events across relevant language keys."""
            if not ctrl_obj:
                return 0
            try:
                with ctrl_obj._epg_cache_lock:
                    cache = ctrl_obj.epg_cache or {}
            except Exception:
                cache = {}
            if not cache:
                return 0

            # Normalize filter refs
            ref_filter = None
            if service_refs:
                try:
                    ref_filter = set([(r.rstrip(":") if hasattr(r, "rstrip") else str(r)) for r in service_refs if r])
                except Exception:
                    ref_filter = None

            # Build candidate language keys (support older/newer layouts)
            try:
                lk_primary = ctrl_obj._epg_lang_key(tcode, dcode)
            except Exception:
                lk_primary = "%s|%s" % (tcode or "", dcode or "")
            langkeys = set([lk_primary])

            # Common case: user enabled title but left descr disabled.
            # Some earlier runs might have stored under title|title; include it so counts don't show 0.
            if tcode and not dcode:
                try:
                    langkeys.add(ctrl_obj._epg_lang_key(tcode, tcode))
                except Exception:
                    langkeys.add("%s|%s" % (tcode, tcode))
                try:
                    langkeys.add(ctrl_obj._epg_lang_key(tcode, ""))
                except Exception:
                    langkeys.add("%s|" % (tcode,))

            # If only descr language is enabled, include a couple of variants
            if dcode and not tcode:
                try:
                    langkeys.add(ctrl_obj._epg_lang_key("", dcode))
                except Exception:
                    langkeys.add("|%s" % (dcode,))
                try:
                    langkeys.add(ctrl_obj._epg_lang_key(dcode, dcode))
                except Exception:
                    langkeys.add("%s|%s" % (dcode, dcode))

            seen = set()
            try:
                for sref, srv in cache.items():
                    try:
                        sref_norm = sref.rstrip(":")
                    except Exception:
                        sref_norm = sref
                    if ref_filter is not None and sref_norm not in ref_filter:
                        continue
                    srv = srv or {}
                    for lk in langkeys:
                        bucket = srv.get(lk) or {}
                        evmap = bucket.get("events") or {}
                        for evk in evmap.keys():
                            seen.add((sref_norm, evk))
            except Exception:
                pass
            return int(len(seen))

        # Total cache count (all services)
        total_cache = 0
        if ctrl and (title_code or descr_code):
            try:
                total_cache = _count_epg_cache(ctrl, title_code, descr_code)
            except Exception:
                total_cache = 0

        # Bouquet name and count
        bname = "Not selected yet"
        bcount = 0
        try:
            bref = (config.plugins.aifury.last_bouquet_ref.value or "").strip()
        except Exception:
            bref = ""
        if bref:
            try:
                bname = _bouquet_name_from_ref(bref) or "Unknown"
            except Exception:
                bname = "Unknown"

            if ctrl and (title_code or descr_code):
                try:
                    srefs = _list_services_in_bouquet(bref) or []
                except Exception:
                    srefs = []
                if srefs:
                    # Normalize srefs to match internal cache keys (rstrip(':'))
                    try:
                        norm_srefs = []
                        for r in srefs:
                            try:
                                norm_srefs.append(r.rstrip(":"))
                            except Exception:
                                norm_srefs.append(r)
                        # de-dup keep order
                        seen2 = set()
                        out2 = []
                        for r in norm_srefs:
                            if r in seen2:
                                continue
                            seen2.add(r)
                            out2.append(r)
                        norm_srefs = out2
                    except Exception:
                        norm_srefs = srefs
                    try:
                        bcount = _count_epg_cache(ctrl, title_code, descr_code, service_refs=norm_srefs)
                    except Exception:
                        bcount = 0

        # Background stats
        bg_translated = 0
        try:
            bg_enabled = bool(config.plugins.aifury.auto_translate_epg.value)
        except Exception:
            bg_enabled = False
        if bg_enabled and ctrl:
            try:
                st = ctrl.get_bg_stats() or {}
            except Exception:
                st = {}
            try:
                bg_translated = int(st.get("translated", 0) or 0)
            except Exception:
                bg_translated = 0

        return "\n".join(
            [
                "EPG Title Language: %s" % title_label,
                "Translated Event Cache: %d" % int(total_cache),
                "Bouquet: %s" % (bname or ""),
                "Translated in Bouquet: %d" % int(bcount),
                "Translated in Background: %d" % int(bg_translated),
            ]
        )
    def keyStats(self):
        try:
            msg = self._build_stats_message()
        except Exception as e:
            msg = "Error building stats:\n%s" % e
        try:
            self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=0)
        except Exception:
            try:
                self.session.open(MessageBox, msg, MessageBox.TYPE_INFO, timeout=6)
            except Exception:
                pass

    def keyInfo(self):
        try:
            self.session.open(AIFuryInfoScreen)
        except Exception as e:
            print("[AIFury] keyInfo error: %s" % e)
            self.session.open(
                MessageBox,
                "Error opening info screen:\n%s" % e,
                MessageBox.TYPE_ERROR,
                timeout=4,
            )


    def keyCancel(self):
        for x in self["config"].list:
            x[1].cancel()
        self.close()


# ---------- plugin entry points ----------

def sessionstart(*args, **kwargs):
    session = None
    if args:
        first = args[0]
        if isinstance(first, int):
            reason = first
            print("[AIFury] sessionstart (reason %s)" % (reason,))
            if len(args) > 1:
                session = args[1]
        else:
            session = first
    else:
        session = kwargs.get("session")

    if session is None:
        print("[AIFury] sessionstart: NO SESSION, controller not created")
        return

    try:
        AIFuryController(session)
        print("[AIFury] Controller created from sessionstart")
    except Exception as e:
        print("[AIFury] ERROR creating Controller: %s" % e)


def main(session, **kwargs):
    try:
        session.open(AIFurySetup)
    except Exception as e:
        print("[AIFury] ERROR opening setup: %s" % e)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name='EpgFury v.%s' % version,
            description="AIFury - EPGTranslation",
            icon="AIFury.png",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            fnc=main,
        ),
        PluginDescriptor(
            where=PluginDescriptor.WHERE_SESSIONSTART,
            fnc=sessionstart,
        ),
    ]