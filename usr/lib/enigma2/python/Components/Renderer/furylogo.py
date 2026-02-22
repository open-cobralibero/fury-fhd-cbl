# -*- coding: utf-8 -*-
# FuryLogo Renderer (Standalone)
# Auto-downloads event logos from TMDB and displays them.
# Save location: /media/hdd/FuryPoster/logo/<CleanEventName>.png
#
# Language strategy (like FuryPosterX idea, extended):
#   1) Search in device language (e.g. pl-PL / pl)
#   2) If not found, search in English (en-US / en)
#   3) If still not found, search without language
#
# Skin usage example:
#   <widget source="session.Event_Now" render="furylogo" position="0,0" size="240,130" transparent="1" alphatest="on" />

# $$$$ This file was created by Islam Salama $$$$ V. 16/1/2026
from __future__ import absolute_import, print_function

import os
import re
import time
import threading
import sys
import hashlib
import json

from Components.Renderer.Renderer import Renderer
from Components.config import config
from enigma import ePixmap, eTimer, loadPNG
import NavigationInstance

PY3 = False
if sys.version_info[0] >= 3:
    PY3 = True
    try:
        from urllib.request import urlopen
    except Exception:
        urlopen = None
else:
    try:
        from urllib2 import urlopen
    except Exception:
        urlopen = None

# ===================== User settings =====================
# Save location(s): the renderer will pick the first available mount.
LOGO_DIRS = [
    "/media/hdd/FuryPoster/logo",
    "/media/hdd1/FuryPoster/logo",
    "/media/hdd2/FuryPoster/logo",
    "/media/usb/FuryPoster/logo",
    "/media/usb1/FuryPoster/logo",
    "/media/usb2/FuryPoster/logo",
    "/media/mmc/FuryPoster/logo",
]


def _pick_logo_dir():
    """Pick the first LOGO_DIRS entry whose mount point exists."""
    for p in LOGO_DIRS:
        try:
            parts = p.strip("/").split("/")
            mount = "/" + "/".join(parts[:2]) if len(parts) >= 2 else os.path.dirname(p)
            if os.path.isdir(mount):
                return p
        except Exception:
            continue
    return LOGO_DIRS[0] if LOGO_DIRS else "/media/hdd/FuryPoster/logo"


LOGO_DIR = _pick_logo_dir()

# TMDB API key (default key ). Better to replace with your own.
TMDB_API_KEY = "3c3efcf47c3577558812bb9d64019d65"

# TMDB logo size on image server: 45, 92, 154, 185, 300, 500, original
TMDB_LOGO_SIZE = "300"

# If download fails for a title, wait this long before retrying
RETRY_COOLDOWN_SEC = 10 * 60

# Cache TTL (speeds up repeated lookups)
CACHE_TTL_SEC = 6 * 60 * 60  # 6 hours

# Optional logging
LOG_ENABLED = False
LOG_FILE = "/tmp/furylogo.log"
# =========================================================

# --- Title cleaning REGEX copied from xtraLogo.py (xtraEvent) ---
REGEX = re.compile(
    r'([\(\[]).*?([\)\]])|'
    r'(: odc.\d+)|'
    r'(\d+: odc.\d+)|'
    r'(\d+ odc.\d+)|(:)|'
    r'!|'
    r'/.*|'
    r'\|\s[0-9]+\+|'
    r'[0-9]+\+|'
    r'\s\d{4}\Z|'
    r'([\(\[\|].*?[\)\]\|])|'
    r'(\"|\"\.|\"\,|\.)\s.+|'
    r'\"|:|'
    r'\*|'
    r'\u041f\u0440\u0435\u043c\u044c\u0435\u0440\u0430\.\s|'
    r'(\u0445|\u0425|\u043c|\u041c|\u0442|\u0422|\u0434|\u0414)/\u0444\s|'
    r'(\u0445|\u0425|\u043c|\u041c|\u0442|\u0422|\u0434|\u0414)/\u0441\s|'
    r'\s(\u0441|\u0421)(\u0435\u0437\u043e\u043d|\u0435\u0440\u0438\u044f|-\u043d|-\u044f)\s.+|'
    r'\s\d{1,3}\s(\u0447|\u0447\.|\u0441\.|\u0441)\s.+|'
    r'\.\s\d{1,3}\s(\u0447|\u0447\.|\u0441\.|\u0441)\s.+|'
    r'\s(\u0447|\u0447\.|\u0441\.|\u0441)\s\d{1,3}.+|'
    r'\d{1,3}(-\u044f|-\u0439|\s\u0441-\u043d).+|'
    r'\s\u062d\s*\d+|'
    r'\s\u062c\s*\d+|'
    r'\s\u0645\s*\d+|'
    r'\d+$'
    , re.DOTALL)


def _log(msg):
    if not LOG_ENABLED:
        return
    try:
        with open(LOG_FILE, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _ensure_dir(path):
    try:
        if not os.path.exists(path):
            os.makedirs(path)
    except Exception as e:
        _log("mkdir error: %s" % e)


# ---------------------------------------------------------------------
# Stable-ID + language helpers (mirrors FuryPosterX behavior)
# ---------------------------------------------------------------------

_tr_cache = {}


def _to_bytes(val):
    try:
        if val is None:
            return b""
        if isinstance(val, bytes):
            return val
        return str(val).encode("utf-8", "ignore")
    except Exception:
        try:
            return bytes(val)
        except Exception:
            return b""


def build_event_uid(service_id, begin_time):
    """Stable uid for an EPG event. Does NOT depend on event title/language."""
    try:
        h = hashlib.md5(_to_bytes(service_id)).hexdigest()[:12]
        bt = int(begin_time) if begin_time is not None else 0
        return "%s_%d" % (h, bt)
    except Exception:
        return None


def ensure_logo_alias(src_path, dst_path):
    """Create dst_path from src_path (hardlink/symlink/copy)."""
    try:
        if not src_path or not dst_path:
            return False
        if not os.path.exists(src_path):
            return False
        if os.path.exists(dst_path):
            return True

        # Prefer hardlink (fast, no duplication) then symlink then copy
        try:
            os.link(src_path, dst_path)
            return True
        except Exception:
            pass
        try:
            os.symlink(src_path, dst_path)
            return True
        except Exception:
            pass
        try:
            import shutil
            shutil.copy2(src_path, dst_path)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _contains_arabic(s):
    try:
        if not s:
            return False
        for ch in s:
            o = ord(ch)
            if (0x0600 <= o <= 0x06FF) or (0x0750 <= o <= 0x077F) or (0x08A0 <= o <= 0x08FF) or (0xFB50 <= o <= 0xFDFF) or (0xFE70 <= o <= 0xFEFF):
                return True
        return False
    except Exception:
        return False


def _is_latin_script(s):
    """Return True if the string is predominantly Latin script (incl. extended Latin)."""
    try:
        if not s:
            return True
        if not isinstance(s, str):
            s = str(s)
        has_alpha = False
        for ch in s:
            o = ord(ch)
            if not ch.isalpha():
                continue
            has_alpha = True
            # Basic Latin + Latin-1 Supplement + Latin Extended-A/B
            if (0x0041 <= o <= 0x024F) or (0x1E00 <= o <= 0x1EFF) or (0x2C60 <= o <= 0x2C7F):
                continue
            return False
        return True if has_alpha else True
    except Exception:
        return True


def translate_to_en(text_in, timeout=3):
    """Translate text to English using Google's free endpoint (best-effort).

    Mirrors FuryPosterX behavior:
      - No translation is attempted for Arabic-script titles.
      - Cached in-memory to reduce network calls.
    """
    try:
        if not text_in:
            return None
        if not isinstance(text_in, str):
            text_in = str(text_in)
        q = text_in.strip()
        if not q or len(q) < 3:
            return None
        if _contains_arabic(q):
            return None

        cached = _tr_cache.get(q)
        if cached is not None:
            return cached

        # If it already looks English-ish, skip network
        try:
            if all(ord(c) < 128 for c in q) and re.search(r"[A-Za-z]{3,}", q):
                _tr_cache[q] = q
                return q
        except Exception:
            pass

        try:
            if PY3:
                from urllib.parse import quote
            else:
                from urllib import quote
        except Exception:
            quote = None

        if quote is None or urlopen is None:
            _tr_cache[q] = None
            return None

        url = (
            "https://translate.googleapis.com/translate_a/single"
            "?client=gtx&sl=auto&tl=en&dt=t&q=" + (quote(q) if PY3 else quote(q.encode('utf-8')))
        )

        try:
            resp = urlopen(url, timeout=timeout)
            data = resp.read()
            try:
                resp.close()
            except Exception:
                pass
        except Exception:
            _tr_cache[q] = None
            return None

        try:
            if isinstance(data, bytes):
                data = data.decode('utf-8', 'ignore')
        except Exception:
            pass

        try:
            j = json.loads(data)
            out = []
            if isinstance(j, list) and j and isinstance(j[0], list):
                for seg in j[0]:
                    try:
                        if isinstance(seg, list) and seg:
                            out.append(seg[0])
                    except Exception:
                        continue
            translated = ''.join(out).strip() if out else None
        except Exception:
            translated = None

        if translated:
            translated = re.sub(r"\s+", " ", translated).strip()

        _tr_cache[q] = translated
        return translated
    except Exception:
        return None


def _get_device_locale():
    """Return locale like 'pl_PL' or 'ar_EG' (best-effort)."""
    try:
        from Components.Language import language
        lang = language.getLanguage()
        if lang:
            return lang
    except Exception:
        pass

    try:
        lang = config.osd.language.value
        if lang:
            return lang
    except Exception:
        pass

    return "en_US"


def _lang_candidates():
    """Generate TMDB language fallbacks."""
    loc = (_get_device_locale() or "en_US").strip()  # e.g. pl_PL
    loc_dash = loc.replace("_", "-")               # pl-PL
    short = loc_dash[:2].lower() if len(loc_dash) >= 2 else "en"

    cands = []

    def add(x):
        if x is None:
            if None not in cands:
                cands.append(None)
            return
        x = str(x).strip()
        if x and x not in cands:
            cands.append(x)

    # Device language first
    add(loc_dash)
    add(short)

    # English fallback
    add("en-US")
    add("en")

    # No language
    add(None)

    return cands


def _event_to_clean_name(evnt):
    """Match xtraLogo cleaning steps (LIVE removal + REGEX)."""
    if not evnt:
        return ""

    try:
        name = evnt.replace('\xc2\x86', '').replace('\xc2\x87', '')
    except Exception:
        name = evnt

    try:
        name = name.replace("live: ", "").replace("LIVE ", "")
        evnt2 = name.replace("live: ", "").replace("LIVE ", "").replace("LIVE: ", "").replace("live ", "")
        evnt2 = name  # keep same as original xtraLogo behavior
    except Exception:
        evnt2 = name

    clean = REGEX.sub('', evnt2).strip()

    # Extra safety: remove forbidden filename chars
    clean = re.sub(r'[\\/:*?"<>|]+', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    return clean


def _title_variants(raw_title, clean_title):
    """Try a few title variants to improve match rate."""
    variants = []

    def add(t):
        if not t:
            return
        t = re.sub(r"\s+", " ", str(t)).strip()
        if t and t not in variants:
            variants.append(t)

    add(clean_title)
    add(raw_title)

    # Before ':' often matches better
    try:
        if raw_title and ":" in raw_title:
            add(raw_title.split(":", 1)[0])
    except Exception:
        pass

    # Remove trailing ' - ...'
    try:
        if raw_title:
            add(re.sub(r"\s*-\s*.+$", "", raw_title).strip())
    except Exception:
        pass

    return variants[:4]


def _logo_path(clean_name):
    if not clean_name:
        return None
    return os.path.join(LOGO_DIR, "%s.png" % clean_name)


def _logo_uid_path(uid):
    if not uid:
        return None
    return os.path.join(LOGO_DIR, "%s.png" % uid)


# ---------- HTTP helpers (requests preferred; urllib fallback) ----------

def _http_get_json(url, timeout=8):
    # requests
    try:
        import requests
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        pass

    # urllib / urllib2
    try:
        try:
            from urllib.request import urlopen, Request
        except Exception:
            from urllib2 import urlopen, Request

        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urlopen(req, timeout=timeout).read()
        try:
            import json
            return json.loads(raw)
        except Exception:
            import json
            return json.loads(raw.decode('utf-8', 'ignore'))
    except Exception:
        return None


def _http_download(url, out_path, timeout=12):
    # requests
    try:
        import requests
        r = requests.get(url, timeout=timeout, stream=True, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return False
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        pass

    # urllib / urllib2
    try:
        try:
            from urllib.request import urlopen, Request
        except Exception:
            from urllib2 import urlopen, Request

        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urlopen(req, timeout=timeout).read()
        with open(out_path, "wb") as f:
            f.write(data)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


# ---------- TMDB logic (multi search -> images logos) ----------

_SEARCH_CACHE = {}  # (title, lang) -> (ts, results[(media_type,id)])
_IMG_CACHE = {}     # (media_type,id) -> (ts, logos_list)


def _quote(s):
    try:
        try:
            from urllib.parse import quote
        except Exception:
            from urllib import quote
        return quote(s)
    except Exception:
        return (s or "").replace(" ", "%20")


def _tmdb_search_multi(title, lang):
    """Return list of (media_type, id) candidates."""
    if not title:
        return []

    now = time.time()
    key = (title, lang)

    try:
        ts, cached = _SEARCH_CACHE.get(key, (0, None))
        if cached is not None and (now - ts) < CACHE_TTL_SEC:
            return cached
    except Exception:
        pass

    q = _quote(title)

    if lang:
        url = "https://api.themoviedb.org/3/search/multi?api_key=%s&query=%s&language=%s" % (TMDB_API_KEY, q, lang)
    else:
        url = "https://api.themoviedb.org/3/search/multi?api_key=%s&query=%s" % (TMDB_API_KEY, q)

    data = _http_get_json(url)
    results = []

    if data and 'results' in data:
        for it in data.get('results', []):
            mt = it.get('media_type')
            if mt in ('movie', 'tv'):
                tid = it.get('id')
                if tid is not None:
                    results.append((mt, tid))
            if len(results) >= 4:
                break

    try:
        _SEARCH_CACHE[key] = (now, results)
    except Exception:
        pass

    return results


def _tmdb_images(media_type, tid):
    """Fetch /images once (cached)."""
    now = time.time()
    key = (media_type, tid)

    try:
        ts, cached = _IMG_CACHE.get(key, (0, None))
        if cached is not None and (now - ts) < CACHE_TTL_SEC:
            return cached
    except Exception:
        pass

    url = "https://api.themoviedb.org/3/%s/%s/images?api_key=%s" % (media_type, tid, TMDB_API_KEY)
    data = _http_get_json(url)

    logos = []
    if data:
        try:
            logos = data.get('logos', []) or []
        except Exception:
            logos = []

    try:
        _IMG_CACHE[key] = (now, logos)
    except Exception:
        pass

    return logos


def _pick_logo_file_path(logos, prefer_iso2):
    """Prefer device iso_639_1 then EN then first."""
    if not logos:
        return None

    if prefer_iso2:
        for item in logos:
            try:
                if item.get('iso_639_1') == prefer_iso2 and item.get('file_path'):
                    return item.get('file_path')
            except Exception:
                pass

    for item in logos:
        try:
            if item.get('iso_639_1') == 'en' and item.get('file_path'):
                return item.get('file_path')
        except Exception:
            pass

    for item in logos:
        try:
            if item.get('file_path'):
                return item.get('file_path')
        except Exception:
            pass

    return None


def _download_logo(uid_path, title_path, clean_name, raw_title):
    """Download logo if possible; return True on success.

    Key behaviors (like FuryPosterX):
      - Save primarily under a stable UID filename (serviceRef+beginTime) so the
        logo does NOT change when EPG language changes.
      - Keep a backward-compatible alias under <CleanEventName>.png.
      - Search with original title first; if not found and title is non-Arabic,
        translate to English and retry.
    """
    if not uid_path and not title_path:
        return False

    _ensure_dir(LOGO_DIR)

    # If either file exists, ensure aliasing both ways and return.
    try:
        if uid_path and os.path.exists(uid_path) and os.path.getsize(uid_path) > 0:
            if title_path and not os.path.exists(title_path):
                ensure_logo_alias(uid_path, title_path)
            return True
        if title_path and os.path.exists(title_path) and os.path.getsize(title_path) > 0:
            if uid_path and not os.path.exists(uid_path):
                ensure_logo_alias(title_path, uid_path)
                if uid_path and os.path.exists(uid_path) and os.path.getsize(uid_path) > 0:
                    return True
            return True
    except Exception:
        pass

    out_path = uid_path or title_path
    if not out_path:
        return False

    titles = _title_variants(raw_title, clean_name)
    langs = _lang_candidates()

    prefer_iso2 = None
    try:
        for l in langs:
            if l:
                prefer_iso2 = str(l)[:2].lower()
                break
    except Exception:
        prefer_iso2 = None

    def _search(title_list, lang_list):
        for t in title_list:
            for lang in lang_list:
                res = _tmdb_search_multi(t, lang)
                if res:
                    return res, t, lang
        return None, None, None

    # Phase 1: original-language title(s)
    best, used_title, used_lang = _search(titles, langs)

    # Phase 2: translate to English (ONLY if not Arabic-script)
    used_translated = False
    if not best:
        trans_src = None
        try:
            trans_src = clean_name or raw_title
        except Exception:
            trans_src = raw_title
        translated = translate_to_en(trans_src)
        if translated and translated not in titles:
            best, used_title, used_lang = _search([translated], ["en-US", "en", None])
            used_translated = bool(best)

    if not best:
        _log("TMDB search failed (original+EN fallback): %s" % (clean_name or raw_title))
        return False

    chosen = None
    file_path = None

    # If we used translation fallback, prefer EN logos first.
    prefer_iso2_eff = "en" if used_translated else prefer_iso2

    for (media_type, tid) in best[:3]:
        logos = _tmdb_images(media_type, tid)
        fp = _pick_logo_file_path(logos, prefer_iso2_eff)
        if fp:
            chosen = (media_type, tid)
            file_path = fp
            break

    if not file_path:
        _log("No TMDB logos for: %s (title=%s lang=%s)" % (clean_name, used_title, used_lang))
        return False

    url_img = "https://image.tmdb.org/t/p/w%s%s" % (TMDB_LOGO_SIZE, file_path)
    tmp = out_path + ".tmp"

    ok = _http_download(url_img, tmp)

    if not ok:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        _log("Download failed: %s" % url_img)
        return False

    try:
        if os.path.exists(out_path):
            os.remove(out_path)
    except Exception:
        pass

    try:
        os.rename(tmp, out_path)
    except Exception:
        try:
            import shutil
            shutil.copyfile(tmp, out_path)
            os.remove(tmp)
        except Exception:
            return False

    # Ensure both uid-based and title-based filenames exist for compatibility.
    try:
        if out_path and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            if title_path and not os.path.exists(title_path):
                ensure_logo_alias(out_path, title_path)
            if uid_path and not os.path.exists(uid_path):
                ensure_logo_alias(out_path, uid_path)
            if title_path and os.path.exists(title_path) and uid_path and not os.path.exists(uid_path):
                ensure_logo_alias(title_path, uid_path)
    except Exception:
        pass

    _log("Saved logo: %s (from %s/%s)" % (out_path, chosen[0], chosen[1] if chosen else "?"))
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


# ---------- Global guards ----------
_IN_PROGRESS = set()
_LAST_FAIL = {}
_LOCK = threading.Lock()


class furylogo(Renderer):
    """Renderer that shows event logo; if missing it downloads from TMDB then displays."""

    GUI_WIDGET = ePixmap

    def __init__(self):
        Renderer.__init__(self)
        self._last_path = None
        self._last_title = None

        # Debounce + polling (like FuryPosterX):
        # - Debounce prevents starting network work during rapid zapping.
        # - Polling updates the pixmap as soon as the background download finishes
        #   without calling eTimer APIs from a worker thread.
        self._pollTimer = eTimer()
        self._debounceTimer = eTimer()
        self._expected_paths = None
        self._poll_deadline = 0
        self._pending_download = None
        try:
            self._pollTimer.callback.append(self._poll_logo)
        except Exception:
            try:
                self._pollTimer.timeout.get().append(self._poll_logo)
            except Exception:
                pass
        try:
            self._debounceTimer.callback.append(self._debounced_fetch)
        except Exception:
            try:
                self._debounceTimer.timeout.get().append(self._debounced_fetch)
            except Exception:
                pass

        self._timer = eTimer()
        try:
            self._timer.callback.append(self._refresh)
        except Exception:
            try:
                self._timer.timeout.get().append(self._refresh)
            except Exception:
                pass

    def _refresh(self):
        try:
            self.changed((self.CHANGED_ALL,))
        except Exception:
            pass

    def changed(self, what):
        if not self.instance:
            return

        if what[0] == self.CHANGED_CLEAR:
            self._last_path = None
            self._last_title = None
            self._expected_paths = None
            self._pending_download = None
            self._poll_deadline = 0
            try:
                self._pollTimer.stop()
            except Exception:
                pass
            try:
                self._debounceTimer.stop()
            except Exception:
                pass
            try:
                self.instance.hide()
            except Exception:
                pass
            return

        event = None
        try:
            event = self.source.event
        except Exception:
            event = None

        if not event:
            try:
                self.instance.hide()
            except Exception:
                pass
            return

        try:
            raw = event.getEventName() or ""
        except Exception:
            raw = ""

        clean = _event_to_clean_name(raw)
        self._last_title = clean

        if not clean:
            try:
                self.instance.hide()
            except Exception:
                pass
            return

        # Build stable uid (serviceRef+beginTime) so saved logo stays the same
        # even if event title changes with EPG language.
        begin_time = None
        try:
            begin_time = event.getBeginTime()
        except Exception:
            begin_time = None

        service_id = None
        try:
            nav = getattr(NavigationInstance, 'instance', None)
            if nav is not None:
                ref = nav.getCurrentlyPlayingServiceReference()
                if ref is not None:
                    try:
                        service_id = ref.toString()
                    except Exception:
                        try:
                            service_id = str(ref)
                        except Exception:
                            service_id = None
        except Exception:
            service_id = None

        uid = build_event_uid(service_id, begin_time)
        uid_path = _logo_uid_path(uid) if uid else None
        title_path = _logo_path(clean)

        # If we already have legacy title logo, alias it to uid for cross-language reuse
        try:
            if title_path and uid_path and os.path.exists(title_path) and not os.path.exists(uid_path):
                ensure_logo_alias(title_path, uid_path)
        except Exception:
            pass

        # Prefer stable uid logo when present
        for path in (uid_path, title_path):
            if path and os.path.exists(path) and os.path.getsize(path) > 0:
                if path != self._last_path:
                    self._last_path = path
                    try:
                        self.instance.setPixmap(loadPNG(path))
                        self.instance.setScale(1)
                    except Exception:
                        pass
                try:
                    self.instance.show()
                except Exception:
                    pass
                # Stop any polling once we have a logo.
                self._expected_paths = None
                self._pending_download = None
                self._poll_deadline = 0
                try:
                    self._pollTimer.stop()
                except Exception:
                    pass
                return

        now = time.time()
        key = uid or clean
        with _LOCK:
            last_fail = _LAST_FAIL.get(key, 0)
            if (now - last_fail) < RETRY_COOLDOWN_SEC:
                try:
                    self.instance.hide()
                except Exception:
                    pass
                return

            # Debounce download start to avoid stutter during fast zapping.
            # We'll schedule the download in ~250ms; if the user zaps again,
            # the pending request will be replaced.
            self._pending_download = (key, uid_path, title_path, clean, raw)

        # Poll for the expected logo file(s) so it appears automatically
        # the moment the download finishes.
        self._expected_paths = [p for p in (uid_path, title_path) if p]
        self._poll_deadline = time.time() + 12  # stop polling after 12s
        try:
            self._pollTimer.start(300, True)
        except Exception:
            pass

        try:
            self._debounceTimer.start(250, True)
        except Exception:
            pass

        try:
            self.instance.hide()
        except Exception:
            pass

    def _bg_download(self, key, uid_path, title_path, clean_name, raw_title):
        ok = False
        try:
            ok = _download_logo(uid_path, title_path, clean_name, raw_title)
        except Exception as e:
            _log("bg error: %s" % e)
            ok = False

        with _LOCK:
            try:
                _IN_PROGRESS.remove(key)
            except Exception:
                pass
            if not ok:
                _LAST_FAIL[key] = time.time()

        # Do not touch eTimer from this thread. UI update is handled by polling.


    def _debounced_fetch(self):
        """Start the background download after a short debounce delay."""
        if not self._pending_download:
            return

        try:
            key, uid_path, title_path, clean_name, raw_title = self._pending_download
        except Exception:
            return

        # If logo arrived during debounce, do nothing.
        for p in (uid_path, title_path):
            try:
                if p and os.path.exists(p) and os.path.getsize(p) > 0:
                    return
            except Exception:
                pass

        with _LOCK:
            if key in _IN_PROGRESS:
                return
            _IN_PROGRESS.add(key)

        t = threading.Thread(target=self._bg_download, args=(key, uid_path, title_path, clean_name, raw_title))
        t.daemon = True
        t.start()


    def _poll_logo(self):
        """Non-blocking poll: when file appears, show it immediately."""
        try:
            if not self.instance:
                return
        except Exception:
            return

        # Stop if no longer waiting.
        if not self._expected_paths:
            return

        # Timeout safety.
        try:
            if self._poll_deadline and time.time() > self._poll_deadline:
                self._expected_paths = None
                self._poll_deadline = 0
                return
        except Exception:
            pass

        # Prefer first existing path.
        found = None
        for p in self._expected_paths:
            try:
                if p and os.path.exists(p) and os.path.getsize(p) > 0:
                    found = p
                    break
            except Exception:
                continue

        if found:
            if found != self._last_path:
                self._last_path = found
                try:
                    self.instance.setPixmap(loadPNG(found))
                    self.instance.setScale(1)
                except Exception:
                    pass
            try:
                self.instance.show()
            except Exception:
                pass

            self._expected_paths = None
            self._poll_deadline = 0
            return

        # Continue polling.
        try:
            self._pollTimer.start(300, True)
        except Exception:
            pass


# Compatibility alias
FuryLogo = furylogo
