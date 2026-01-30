#!/usr/bin/python
# -*- coding: utf-8 -*-

# recode from Islam Salama 2026


# Improving the performance of information and poster retrieval by Islam Salama
# by digiteng...07.2021,
# 08.2021(stb lang support),
# 09.2021 mini fixes
# edit by lululla 07.2022
# recode from lululla 2023
# © Provided that digiteng rights are protected, all or part of the code can be used, modified...
# russian and py3 support by sunriser...
# downloading in the background while zaping...
# by beber...03.2022,
# 03.2022 several enhancements : several renders with one queue thread, google search (incl. molotov for france) + autosearch & autoclean thread ...
# for infobar,
# <widget source="session.Event_Now" render="furyPosterX" position="100,100" size="185,278" />
# <widget source="session.Event_Next" render="furyPosterX" position="100,100" size="100,150" />
# <widget source="session.Event_Now" render="furyPosterX" position="100,100" size="185,278" nexts="2" />
# <widget source="session.CurrentService" render="furyPosterX" position="100,100" size="185,278" nexts="3" />

# for ch,
# <widget source="ServiceEvent" render="furyPosterX" position="100,100" size="185,278" />
# <widget source="ServiceEvent" render="furyPosterX" position="100,100" size="185,278" nexts="2" />

# for epg, event
# <widget source="Event" render="furyPosterX" position="100,100" size="185,278" />
# <widget source="Event" render="furyPosterX" position="100,100" size="185,278" nexts="2" />
# or put tag -->  path="/media/hdd/poster"
from __future__ import print_function
from Components.Renderer.Renderer import Renderer
from Components.Renderer.furyPosterXDownloadThread import furyPosterXDownloadThread
from Components.Sources.CurrentService import CurrentService
from Components.Sources.Event import Event
from Components.Sources.EventInfo import EventInfo
from Components.Sources.ServiceEvent import ServiceEvent
from Components.config import config
from ServiceReference import ServiceReference
from enigma import (
    ePixmap,
    loadJPG,
    eEPGCache,
    eTimer,
)
import NavigationInstance
import os
import socket
import sys
import hashlib
import time
import traceback
import datetime
import threading
from .furyConverlibr import convtext

PY3 = False
if sys.version_info[0] >= 3:
    PY3 = True
    import queue
    from _thread import start_new_thread
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen
else:
    import Queue
    from thread import start_new_thread
    from urllib2 import HTTPError, URLError
    from urllib2 import urlopen


epgcache = eEPGCache.getInstance()
if PY3:
    pdb = queue.LifoQueue()
else:
    pdb = Queue.LifoQueue()

# Background (slow) poster queue: used to try additional providers without blocking zapping
try:
    pauto = queue.Queue()
except Exception:
    try:
        pauto = Queue.Queue()
    except Exception:
        pauto = None

_bg_pending = set()
_bg_pending_lock = threading.Lock()

def enqueue_bg(canal):
    """Enqueue a canal item for background (slow) provider lookup, deduplicated by uid."""
    global pauto
    if pauto is None or not canal:
        return
    try:
        service_name = canal[0]
        begin_time = canal[1]
        service_id = apdb.get(service_name, service_name)
        uid = build_event_uid(service_id, begin_time)
        key = uid or (service_name, begin_time, canal[5])
        with _bg_pending_lock:
            if key in _bg_pending:
                return
            _bg_pending.add(key)
        pauto.put(canal)
    except Exception:
        # Best-effort only
        pass



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


def _strip_diacritics_ar(s):
    # Remove Arabic diacritics and tatweel; keep base letters
    try:
        if not s:
            return s
        # Arabic diacritics
        diacs = set([
            u'\u064b', u'\u064c', u'\u064d', u'\u064e', u'\u064f', u'\u0650', u'\u0651', u'\u0652',
            u'\u0653', u'\u0654', u'\u0655', u'\u0670', u'\u0640'
        ])
        return u"".join(ch for ch in s if ch not in diacs)
    except Exception:
        return s


def _normalize_arabic(s):
    try:
        if not s:
            return s
        s = _strip_diacritics_ar(s)
        # Normalize common variants
        s = s.replace(u'أ', u'ا').replace(u'إ', u'ا').replace(u'آ', u'ا')
        s = s.replace(u'ى', u'ي').replace(u'ؤ', u'و').replace(u'ئ', u'ي')
        s = s.replace(u'ة', u'ه')  # helps loose matching for some sources
        return s
    except Exception:
        return s


def _clean_title_common(s):
    """Remove common noise: episode markers, LIVE/REPEAT tags, bracketed metadata."""
    try:
        if not s:
            return s
        # Remove control chars used by Enigma2
        s = s.replace('\xc2\x86', '').replace('\xc2\x87', '')
        # Normalize whitespace
        s = re.sub(r'\s+', ' ', s).strip()

        # Remove bracketed parts that often add noise
        s = re.sub(r'\[[^\]]{0,60}\]', ' ', s)
        s = re.sub(r'\([^\)]{0,60}\)', ' ', s)

        # Remove common episode/season markers (Arabic + English)
        s = re.sub(r'\bS\d{1,2}\s*E\d{1,3}\b', ' ', s, flags=re.IGNORECASE)
        s = re.sub(r'\b(?:EP|E)\s*\d{1,4}\b', ' ', s, flags=re.IGNORECASE)
        s = re.sub(r'حلقة\s*\d{1,4}', ' ', s)
        s = re.sub(r'الموسم\s*\d{1,2}', ' ', s)
        s = re.sub(r'الجزء\s*\d{1,2}', ' ', s)

        # Remove LIVE/REPEAT and similar tags
        s = re.sub(r'\b(LIVE|REPLAY|NEW)\b', ' ', s, flags=re.IGNORECASE)
        s = re.sub(r'(مباشر|اعاده|إعاده|إعادة|حصري|جديد)\b', ' ', s)

        # Collapse spaces again
        s = re.sub(r'\s+', ' ', s).strip()
        return s
    except Exception:
        return s


def _extract_latin_candidates(*parts):
    """Extract likely English title fragments from title/shortdesc/fulldesc."""
    out = []
    try:
        for s in parts:
            if not s:
                continue
            try:
                if not isinstance(s, str):
                    s = str(s)
            except Exception:
                continue
            # Pull phrases like "The Movie", "Game of Thrones", "UFC 300"
            for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9'&:\- ]{2,80}", s):
                frag = m.group(0).strip()
                # filter trivial fragments
                if len(frag) < 4:
                    continue
                if frag.lower() in ("live", "replay", "new"):
                    continue
                out.append(frag)
        # Deduplicate preserving order
        uniq = []
        seen = set()
        for x in out:
            k = x.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        return uniq[:6]
    except Exception:
        return out[:3]


def build_title_candidates(title, shortdesc=None, fulldesc=None, channel=None):
    """
    Return two lists:
      - all_candidates: title variants (Arabic and/or English), ordered by likelihood
      - latin_first: same list but with pure-latin candidates prioritized (better for TVDB/IMDB)
    """
    try:
        base = title or ""
        if not isinstance(base, str):
            base = str(base)

        raw = base.strip()
        raw = raw.replace('\xc2\x86', '').replace('\xc2\x87', '').strip()

        candidates = []
        def _add(x):
            if not x:
                return
            if not isinstance(x, str):
                try:
                    x = str(x)
                except Exception:
                    return
            x = re.sub(r'\s+', ' ', x).strip()
            if len(x) < 3:
                return
            # de-dup
            if x not in candidates:
                candidates.append(x)

        # 1) Raw
        _add(raw)

        # 2) Clean common noise
        cleaned = _clean_title_common(raw)
        _add(cleaned)

        # 3) Arabic normalization (helps loose matching)
        if _contains_arabic(cleaned):
            _add(_normalize_arabic(cleaned))

        # 4) English fragments from title/desc
        latin = _extract_latin_candidates(raw, shortdesc, fulldesc)
        for x in latin:
            _add(x)

        # 5) Some EPGs include English after dash or colon; try last segment
        if raw and ("-" in raw or ":" in raw):
            tail = re.split(r"[-:]", raw)[-1].strip()
            _add(tail)

        # 6) Disambiguate with channel name for Google sources (kept later)
        if channel:
            try:
                ch = channel if isinstance(channel, str) else str(channel)
                ch = re.sub(r'\s+', ' ', ch).strip()
                if ch and raw and ch not in raw:
                    _add(raw + " " + ch)
                if ch and cleaned and ch not in cleaned:
                    _add(cleaned + " " + ch)
            except Exception:
                pass

        # Build latin-first ordering
        def is_pure_latin(s):
            return (not _contains_arabic(s)) and bool(re.search(r"[A-Za-z]", s))

        latin_first = []
        for x in candidates:
            if is_pure_latin(x) and x not in latin_first:
                latin_first.append(x)
        for x in candidates:
            if x not in latin_first:
                latin_first.append(x)

        return candidates[:10], latin_first[:10]
    except Exception:
        try:
            return [title] if title else [], [title] if title else []
        except Exception:
            return [], []


def ensure_poster_alias(src_path, dst_path):
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



def isMountedInRW(mount_point):
    with open("/proc/mounts", "r") as f:
        for line in f:
            parts = line.split()
            if len(parts) > 1 and parts[1] == mount_point:
                return True
    return False


cur_skin = config.skin.primary_skin.value.replace('/skin.xml', '')
noposter = "/usr/share/enigma2/%s/main/noposter.png" % cur_skin
path_folder = "/tmp/poster"
if os.path.exists("/media/hdd"):
    if isMountedInRW("/media/hdd"):
        path_folder = "/media/hdd/FuryPoster/poster"
elif os.path.exists("/media/usb"):
    if isMountedInRW("/media/usb"):
        path_folder = "/media/usb/FuryPoster/poster"
elif os.path.exists("/media/mmc"):
    if isMountedInRW("/media/mmc"):
        path_folder = "/media/mmc/FuryPoster/poster"

if not os.path.exists(path_folder):
    os.makedirs(path_folder)


epgcache = eEPGCache.getInstance()
apdb = dict()


try:
    lng = config.osd.language.value
    lng = lng[:-3]
except:
    lng = 'en'
    pass


# SET YOUR PREFERRED BOUQUET FOR AUTOMATIC POSTER GENERATION
# WITH THE NUMBER OF ITEMS EXPECTED (BLANK LINE IN BOUQUET CONSIDERED)
# IF NOT SET OR WRONG FILE THE AUTOMATIC POSTER GENERATION WILL WORK FOR
# THE CHANNELS THAT YOU ARE VIEWING IN THE ENIGMA SESSION

def SearchBouquetTerrestrial():
    import glob
    import codecs
    file = '/etc/enigma2/userbouquet.favourites.tv'
    for file in sorted(glob.glob('/etc/enigma2/*.tv')):
        with codecs.open(file, "r", encoding="utf-8") as f:
            file = f.read()
            x = file.strip().lower()
            if x.find('eeee') != -1:
                if x.find('82000') == -1 and x.find('c0000') == -1:
                    return file
                    break


autobouquet_file = None


def process_autobouquet():
    global autobouquet_file
    autobouquet_file = SearchBouquetTerrestrial() or '/etc/enigma2/userbouquet.favourites.tv'
    autobouquet_count = 70
    apdb = {}

    if not os.path.exists(autobouquet_file):
        print("File non trovato:", autobouquet_file)
        return {}

    try:
        with open(autobouquet_file, 'r') as f:
            lines = f.readlines()
    except (IOError, OSError) as e:
        print("Errore nella lettura del file:", e)
        return {}

    autobouquet_count = min(autobouquet_count, len(lines))

    for i, line in enumerate(lines[:autobouquet_count]):
        if line.startswith('#SERVICE'):
            parts = line[9:].strip().split(':')
            if len(parts) == 11 and ':'.join(parts[3:7]) != '0:0:0:0':
                apdb[i] = ':'.join(parts)

    print("Trovati", len(apdb), "servizi validi.")
    return apdb


# NOTE: Autoboquet parsing is deferred to runtime to avoid slowing down Enigma2 startup.
# Cached internet check (avoid blocking Enigma2 startup / widget init)
_adsl_ok = None

def intCheck(timeout=1):
    """Return True if an internet connection appears available.

    The timeout is intentionally short to avoid UI stalls during boot.
    """
    try:
        response = urlopen("http://google.com", None, timeout)
        response.close()
        return True
    except (HTTPError, URLError, socket.timeout, Exception):
        return False


def intCheck_cached(timeout=1):
    """Cache the result of intCheck() so multiple widgets don't repeat blocking I/O."""
    global _adsl_ok
    if _adsl_ok is None:
        _adsl_ok = intCheck(timeout=timeout)
    return _adsl_ok



class PosterDB(furyPosterXDownloadThread):
    def __init__(self):
        furyPosterXDownloadThread.__init__(self)
        self.logdbg = None
        self.pstcanal = None

    def run(self):
        self.logDB("[QUEUE] : Initialized")
        while True:
            canal = pdb.get()
            self.logDB("[QUEUE] : {} : {}-{} ({})".format(canal[0], canal[1], canal[2], canal[5]))
            self.pstcanal = convtext(canal[5]) if canal[5] else None

            # Title-based path (legacy)
            title_path = os.path.join(path_folder, str(self.pstcanal) + ".jpg") if self.pstcanal else None

            # Stable uid-based path (preferred)
            service_name = canal[0]
            begin_time = canal[1]
            service_id = apdb.get(service_name, service_name)
            uid = build_event_uid(service_id, begin_time)
            uid_path = os.path.join(path_folder, str(uid) + ".jpg") if uid else title_path

            # If we already have legacy title poster, alias it to uid for cross-language reuse
            if title_path and uid_path and os.path.exists(title_path) and not os.path.exists(uid_path):
                ensure_poster_alias(title_path, uid_path)

            # Download target is uid_path when available; otherwise fallback to legacy
            dwn_poster = uid_path or title_path

            if not dwn_poster:
                self.logDB("[ERROR] Invalid poster target (None)")
                continue

            # FAST lookup while standing on a channel: only try TMDB + TVDB to reduce latency.
            # Build robust title candidates (Arabic/English) from EPG title + descriptions
            all_titles, latin_titles = build_title_candidates(canal[5], canal[4], canal[3], canal[0])
            search_methods = []
            for _name in ("search_tmdb", "search_tvdb"):
                if hasattr(self, _name):
                    search_methods.append(getattr(self, _name))
            if not search_methods:
                # Safety net
                search_methods = [self.search_tmdb]

            for search_method in search_methods:
                if not os.path.exists(dwn_poster):
                    # TVDB/IMDB generally perform better with English (latin) queries when available.
                    cand_list = latin_titles if search_method.__name__ in ("search_tvdb", "search_imdb") else all_titles
                    for cand in cand_list[:3]:
                        if os.path.exists(dwn_poster):
                            break
                        result = search_method(dwn_poster, cand, canal[4], canal[3], canal[0])

                        if result is None:
                            self.logDB("[ERROR] Search method '{}' returned None".format(search_method.__name__))
                            continue

                        try:
                            val, log = result
                        except ValueError:
                            self.logDB("[ERROR] Unexpected result from '{}': {}".format(search_method.__name__, result))
                            continue

                        self.logDB(log)
                        if "SUCCESS" in log:
                            break
            # Ensure both uid-based and title-based filenames exist for compatibility
            try:
                if dwn_poster and os.path.exists(dwn_poster) and title_path and not os.path.exists(title_path):
                    ensure_poster_alias(dwn_poster, title_path)
                if title_path and os.path.exists(title_path) and uid_path and not os.path.exists(uid_path):
                    ensure_poster_alias(title_path, uid_path)
            except Exception:
                pass
            # Still missing after fast providers: hand off to background (slow) providers.
            if dwn_poster and not os.path.exists(dwn_poster):
                enqueue_bg(canal)


    def logDB(self, logmsg):
        try:
            with open("/tmp/PosterDB.log", "a") as w:
                w.write("%s\n" % logmsg)
        except Exception as e:
            print("logDB error:", str(e))
            traceback.print_exc()


# NOTE: PosterDB thread is started lazily (see init_bg_once()).
threadDB = None
class PosterAutoDB(furyPosterXDownloadThread):
    def __init__(self):
        furyPosterXDownloadThread.__init__(self)
        self.logdbg = None
        self.pstcanal = None

    def run(self):
        self.logAutoDB("[AutoDB] *** Initialized ***")
        # Two roles:
        #  1) On-demand background provider fallback (slow sources only) triggered by channel zap logic.
        #  2) Periodic library refresh (full scan) every 2 hours.
        next_scan = time.time() + 7200

        def _compute_paths(canal):
            pst = convtext(canal[5]) if canal and len(canal) > 5 and canal[5] else None
            title_path = os.path.join(path_folder, str(pst) + ".jpg") if pst else None
            service_name = canal[0] if canal else None
            begin_time = canal[1] if canal else None
            service_id = apdb.get(service_name, service_name)
            uid = build_event_uid(service_id, begin_time)
            uid_path = os.path.join(path_folder, str(uid) + ".jpg") if uid else title_path
            return pst, title_path, uid_path, (uid_path or title_path), uid, service_id

        def _run_sources(canal, source_names):
            try:
                self.pstcanal, title_path, uid_path, dwn_poster, uid, _service_id = _compute_paths(canal)
                if not dwn_poster:
                    return

                # Ensure uid/title aliasing when one exists
                try:
                    if title_path and uid_path and os.path.exists(title_path) and not os.path.exists(uid_path):
                        ensure_poster_alias(title_path, uid_path)
                except Exception:
                    pass

                # Build robust title candidates (Arabic/English) for background fallbacks
                all_titles, latin_titles = build_title_candidates(canal[5], canal[4], canal[3], canal[0])
                for _name in source_names:
                    if os.path.exists(dwn_poster):
                        break
                    if not hasattr(self, _name):
                        continue
                    fn = getattr(self, _name)
                    # Try multiple title variants for better coverage (Arabic/English EPG).
                    # Prefer latin queries for sources that usually index English titles.
                    cand_list = latin_titles if _name in ("search_tvdb", "search_fanart", "search_imdb") else all_titles
                    max_try = 2 if _name in ("search_tmdb", "search_tvdb") else 4
                    for cand in cand_list[:max_try]:
                        if os.path.exists(dwn_poster):
                            break
                        result = fn(dwn_poster, cand, canal[4], canal[3], canal[0])
                        if result is None:
                            continue
                        try:
                            _val, log = result
                        except Exception:
                            continue
                        self.logAutoDB(log)
                        if "SUCCESS" in log:
                            break
                # Final alias for compatibility
                try:
                    if dwn_poster and os.path.exists(dwn_poster) and title_path and not os.path.exists(title_path):
                        ensure_poster_alias(dwn_poster, title_path)
                    if title_path and os.path.exists(title_path) and uid_path and not os.path.exists(uid_path):
                        ensure_poster_alias(title_path, uid_path)
                except Exception:
                    pass
            except Exception:
                traceback.print_exc()

        slow_sources = (
            "search_fanart",
            "search_imdb",
            "search_programmetv_google",
            "search_molotov_google",
            "search_google",
        )
        full_sources = (
            "search_tmdb",
            "search_tvdb",
            "search_fanart",
            "search_imdb",
            "search_programmetv_google",
            "search_molotov_google",
            "search_google",
        )

        while True:
            # 1) On-demand slow lookups (do not block channel zap)
            canal = None
            if pauto is not None:
                try:
                    canal = pauto.get(timeout=15)
                except Exception:
                    canal = None

            if canal:
                _run_sources(canal, slow_sources)
                # Clear pending marker
                try:
                    _pst, _tpath, _upath, _dwn, uid, _sid = _compute_paths(canal)
                    key = uid or (canal[0], canal[1], canal[5])
                    with _bg_pending_lock:
                        _bg_pending.discard(key)
                except Exception:
                    pass
                continue

            # 2) Periodic full scan every 2 hours
            if time.time() < next_scan:
                continue

            self.logAutoDB("[AutoDB] *** Running ***")
            self.pstcanal = None

            with _apdb_lock:
                services = list(apdb.values())

            for service in services:
                try:
                    events = epgcache.lookupEvent(['IBDCTESX', (service, 0, -1, 1440)])
                    newfd = 0
                    newcn = None
                    for evt in events or []:
                        canal_evt = [None] * 6
                        try:
                            if PY3:
                                canal_evt[0] = ServiceReference(service).getServiceName().replace('\xc2\x86', '').replace('\xc2\x87', '')
                            else:
                                canal_evt[0] = ServiceReference(service).getServiceName().replace('\\xc2\\x86', '').replace('\\xc2\\x87', '')
                        except Exception:
                            canal_evt[0] = service
                        canal_evt[1] = evt[1]
                        canal_evt[2] = evt[2]
                        canal_evt[3] = evt[3]
                        canal_evt[4] = evt[4]
                        canal_evt[5] = evt[5]

                        _pst, tpath, upath, dwn_poster, _uid, _sid = _compute_paths(canal_evt)
                        if (tpath and os.path.exists(tpath)) or (upath and os.path.exists(upath)):
                            continue

                        if newcn is None:
                            newcn = canal_evt[0]
                        if newcn != canal_evt[0]:
                            newcn = canal_evt[0]
                            newfd = 0
                        if newfd >= 8:
                            continue

                        _run_sources(canal_evt, full_sources)
                        if dwn_poster and os.path.exists(dwn_poster):
                            newfd += 1
                except Exception:
                    traceback.print_exc()

            try:
                now_tm = time.time()
                emptyfd = 0
                oldfd = 0
                for f in os.listdir(path_folder):
                    file_path = os.path.join(path_folder, f)
                    diff_tm = now_tm - os.path.getmtime(file_path)
                    if diff_tm > 120 and os.path.getsize(file_path) == 0:
                        os.remove(file_path)
                        emptyfd += 1
                    elif diff_tm > 31536000:
                        os.remove(file_path)
                        oldfd += 1
                self.logAutoDB("[AutoDB] {} old file(s) removed".format(oldfd))
                self.logAutoDB("[AutoDB] {} empty file(s) removed".format(emptyfd))
            except Exception:
                traceback.print_exc()

            self.logAutoDB("[AutoDB] *** Stopping ***")
            next_scan = time.time() + 7200

    def logAutoDB(self, logmsg):
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open("/tmp/PosterAutoDB.log", "a") as w:
                w.write("[{}] {}\n".format(timestamp, logmsg))
        except Exception as e:
            print("logAutoDB error: {}".format(e))
            traceback.print_exc()


# NOTE: PosterAutoDB thread is started lazily (see init_bg_once()).
threadAutoDB = None

# ---------------------------------------------------------------------
# Deferred initialization (reduce Enigma2 boot time)
# ---------------------------------------------------------------------
_bg_inited = False
_bg_init_lock = threading.Lock()
_apdb_lock = threading.Lock()

def _load_autobouquet_bg():
    """Parse bouquets in background and populate apdb."""
    global apdb
    try:
        data = process_autobouquet()
        if isinstance(data, dict):
            with _apdb_lock:
                apdb.update(data)
    except Exception:
        traceback.print_exc()


def init_bg_once():
    """Initialize heavy background resources once (threads + bouquet parsing)."""
    global _bg_inited, threadDB, threadAutoDB
    if _bg_inited:
        return
    with _bg_init_lock:
        if _bg_inited:
            return
        _bg_inited = True

        # Load bouquets in the background to avoid blocking UI.
        try:
            t = threading.Thread(target=_load_autobouquet_bg, name="furyPosterX-Autobouquet")
            try:
                t.daemon = True
            except Exception:
                t.setDaemon(True)
            t.start()
        except Exception:
            traceback.print_exc()

        # Start poster download threads lazily.
        try:
            if threadDB is None:
                threadDB = PosterDB()
                threadDB.start()
        except Exception:
            traceback.print_exc()

        try:
            if threadAutoDB is None:
                threadAutoDB = PosterAutoDB()
                threadAutoDB.start()
        except Exception:
            traceback.print_exc()


class furyPosterX(Renderer):
    def __init__(self):
        Renderer.__init__(self)
        self.adsl = intCheck_cached()
        if not self.adsl:
            print("Connessione assente, modalità offline.")
            return
        else:
            print("Connessione rilevata.")
        self.nxts = 0
        self.path = path_folder  # + '/'
        self.canal = [None, None, None, None, None, None]
        self.oldCanal = None
        self.pstrNm = None
        self.logdbg = None
        self.pstcanal = None
        self.timer = eTimer()
        self.service_str = None
        try:
            self.timer_conn = self.timer.timeout.connect(self.showPoster)
        except:
            self.timer.callback.append(self.showPoster)

        # Non-blocking polling to update poster as soon as download finishes (no threads/sleep)
        self._pollTimer = eTimer()
        self._debounceTimer = eTimer()
        self._expected_paths = None
        self._poll_deadline = 0
        self._pending_canal = None
        try:
            self._pollTimer_conn = self._pollTimer.timeout.connect(self._pollPoster)
        except:
            self._pollTimer.callback.append(self._pollPoster)
        try:
            self._debounce_conn = self._debounceTimer.timeout.connect(self._debouncedFetch)
        except:
            self._debounceTimer.callback.append(self._debouncedFetch)

    def applySkin(self, desktop, parent):
        attribs = []
        for (attrib, value,) in self.skinAttributes:
            if attrib == "nexts":
                self.nxts = int(value)
            if attrib == "path":
                self.path = str(value)
            attribs.append((attrib, value))
        self.skinAttributes = attribs
        return Renderer.applySkin(self, desktop, parent)

    GUI_WIDGET = ePixmap

    def changed(self, what):
        if not self.instance:
            return
        if what[0] == self.CHANGED_CLEAR:
            self.instance.hide()
            return

        # Deferred heavy init (threads + bouquet parsing)
        init_bg_once()

        servicetype = None
        try:
            service = None
            source_type = type(self.source)
            if source_type is ServiceEvent:  # source="ServiceEvent"
                service = self.source.getCurrentService()
                servicetype = "ServiceEvent"
            elif source_type is CurrentService:  # source="session.CurrentService"
                service = self.source.getCurrentServiceRef()
                servicetype = "CurrentService"
            elif source_type is EventInfo:  # source="session.Event_Now" or source="session.Event_Next"
                service = NavigationInstance.instance.getCurrentlyPlayingServiceReference()
                servicetype = "EventInfo"
            elif source_type is Event:  # source="Event"
                if self.nxts:
                    service = NavigationInstance.instance.getCurrentlyPlayingServiceReference()
                else:
                    self.canal[0] = None
                    self.canal[1] = self.source.event.getBeginTime()
                    event_name = self.source.event.getEventName().replace('\xc2\x86', '').replace('\xc2\x87', '')
                    if not PY3:
                        event_name = event_name.encode('utf-8')
                    self.canal[2] = event_name
                    self.canal[3] = self.source.event.getExtendedDescription()
                    self.canal[4] = self.source.event.getShortDescription()
                    self.canal[5] = event_name
                servicetype = "Event"
            if service is not None:
                service_str = service.toString()
                self.service_str = service_str
                events = epgcache.lookupEvent(['IBDCTESX', (service_str, 0, -1, -1)])
                if not events or len(events) <= self.nxts or events[self.nxts] is None:
                    raise Exception("No EPG events for service (nxts={})".format(self.nxts))
                service_name = ServiceReference(service).getServiceName().replace('\xc2\x86', '').replace('\xc2\x87', '')
                if not PY3:
                    service_name = service_name.encode('utf-8')
                self.canal[0] = service_name
                self.canal[1] = events[self.nxts][1]
                self.canal[2] = events[self.nxts][4]
                self.canal[3] = events[self.nxts][5]
                self.canal[4] = events[self.nxts][6]
                self.canal[5] = self.canal[2]

                if not autobouquet_file:
                    with _apdb_lock:
                        if service_name not in apdb:
                            apdb[service_name] = service_str

        except Exception as e:
            print("Error (service):", str(e))
            if self.instance:
                self.instance.hide()
            return
        if not servicetype:
            print("Error: service type undefined")
            if self.instance:
                self.instance.hide()
            return

        try:
            # Unique key includes service ref to avoid sticky posters between channels
            curCanal = "{}-{}-{}".format(self.service_str or "", self.canal[1], self.canal[2])
            if curCanal == self.oldCanal:
                return

            self.oldCanal = curCanal
            self.logPoster("Service: {} [{}] : {} : {}".format(servicetype, self.nxts, self.canal[0], self.oldCanal))

            # Stop any previous polling/debounce (zapping fast)
            try:
                self._pollTimer.stop()
                self._debounceTimer.stop()
            except Exception:
                pass

            # Build expected poster filenames
            title_path = None
            try:
                pst_title = convtext(self.canal[5]) if self.canal and len(self.canal) > 5 else None
                title_path = os.path.join(self.path, str(pst_title) + ".jpg") if pst_title else None
            except Exception:
                title_path = None

            service_id = self.service_str or apdb.get(self.canal[0], self.canal[0])
            uid = build_event_uid(service_id, self.canal[1])
            uid_path = os.path.join(self.path, str(uid) + ".jpg") if uid else title_path

            self._expected_paths = [p for p in (uid_path, title_path) if p]

            # If we already have it, show immediately
            existing = None
            if uid_path and os.path.exists(uid_path):
                existing = uid_path
            elif title_path and os.path.exists(title_path):
                # legacy -> alias to uid for reuse
                if uid_path:
                    ensure_poster_alias(title_path, uid_path)
                    if os.path.exists(uid_path):
                        existing = uid_path
                if existing is None:
                    existing = title_path

            if existing:
                self.pstrNm = existing
                self.timer.start(10, True)
            else:
                # Hide old poster to avoid 'sticking' while new one downloads
                if self.instance:
                    self.instance.hide()
                self._pending_canal = self.canal[:]
                # small debounce to avoid hammering downloads while zapping
                self._debounceTimer.start(200, True)

        except Exception as e:
            print("Error (eFile):", str(e))
            if self.instance:
                self.instance.hide()
            return

    def generatePosterPath(self):
        """Generate poster path with stable UID (channel+start time) and backward-compatible title fallback."""
        title_path = None
        if self.canal and len(self.canal) > 5 and self.canal[5]:
            try:
                pst_title = convtext(self.canal[5])
                title_path = os.path.join(self.path, str(pst_title) + ".jpg")
            except Exception:
                title_path = None

        service_id = self.service_str or (self.canal[0] if self.canal else None)
        uid = build_event_uid(service_id, self.canal[1] if self.canal else None)
        uid_path = os.path.join(self.path, str(uid) + ".jpg") if uid else title_path

        # Prefer stable uid
        if uid_path and os.path.exists(uid_path):
            return uid_path

        # Backward compatibility: if title-based exists, alias it to uid and use uid
        if title_path and os.path.exists(title_path):
            if uid_path:
                ensure_poster_alias(title_path, uid_path)
                if os.path.exists(uid_path):
                    return uid_path
            return title_path

        # Default to uid path (so waiting/downloading targets stable file)
        return uid_path or title_path


    def _debouncedFetch(self):
        """Run download after a short debounce, then poll for the resulting file."""
        canal = self._pending_canal
        self._pending_canal = None
        if not canal:
            return
        try:
            pdb.put(canal)
        except Exception as e:
            self.logPoster("[ERROR: enqueue] {}".format(e))
            return
        self._startPosterPoll()

    def _startPosterPoll(self):
        """Poll for poster file existence using a debounced/backoff timer.

        Tuning goal:
          - Fast first paint (check frequently early)
          - Low I/O long-tail (backoff later)
        """
        self._poll_started_at = time.time()
        self._poll_deadline = self._poll_started_at + 60.0  # seconds
        # Fast initial cadence to reduce perceived latency after download finishes
        self._poll_next_ms = 200
        try:
            # Kick an immediate single-shot check
            self._pollTimer.start(self._poll_next_ms, True)
        except Exception:
            pass

    def _pollPoster(self):
        try:
            paths = self._expected_paths or []
            for p in paths:
                if p and os.path.exists(p):
                    self.pstrNm = p
                    self.timer.start(10, True)
                    return

            # Not found yet: stop if we exceeded the deadline
            if time.time() > (self._poll_deadline or 0):
                try:
                    self._pollTimer.stop()
                except Exception:
                    pass
                # If nothing was found, show the skin's noposter instead of leaving the widget empty
                try:
                    if noposter and os.path.exists(noposter):
                        self.pstrNm = noposter
                        self.timer.start(10, True)
                except Exception:
                    pass
                return

            # Backoff polling to reduce filesystem pressure during zapping / HDD spin-up
            try:
                elapsed = time.time() - float(getattr(self, "_poll_started_at", time.time()))
                # Early phase: keep checks frequent for fast first paint.
                if elapsed < 10.0:
                    cap = 800
                elif elapsed < 30.0:
                    cap = 2000
                else:
                    cap = 5000

                nxt = int(getattr(self, "_poll_next_ms", 200) * 1.25)
                if nxt < 200:
                    nxt = 200
                self._poll_next_ms = cap if nxt > cap else nxt
            except Exception:
                self._poll_next_ms = 1000

            try:
                self._pollTimer.start(self._poll_next_ms, True)  # single-shot
            except Exception:
                pass

        except Exception as e:
            self.logPoster("[ERROR: poll] {}".format(e))
            try:
                self._pollTimer.stop()
            except Exception:
                pass

    def showPoster(self):
        if self.instance:
            self.instance.hide()

        self.pstrNm = self.generatePosterPath()

        # Show poster if present, otherwise show default noposter (if available)
        target = None
        if self.pstrNm and os.path.exists(self.pstrNm):
            target = self.pstrNm
        elif noposter and os.path.exists(noposter):
            target = noposter

        if target and self.instance:
            self.logPoster("[LOAD : showPoster] " + target)
            self.instance.setPixmap(loadJPG(target))
            self.instance.setScale(1)
            self.instance.show()

    def waitPoster(self):
        if self.instance:
            self.instance.hide()

        self.pstrNm = self.generatePosterPath()
        if not self.pstrNm:
            self.logPoster("[ERROR: waitPoster] Poster path is None")
            return
        loop = 180  # Numero massimo di tentativi
        found = False
        self.logPoster("[LOOP: waitPoster] " + self.pstrNm)
        while loop > 0:
            if self.pstrNm and os.path.exists(self.pstrNm):
                found = True
                break
            time.sleep(0.5)
            loop -= 1
        if found:
            self.timer.start(10, True)

    def logPoster(self, logmsg):
        import traceback
        try:
            with open("/tmp/logPosterXx.log", "a") as w:
                w.write("%s\n" % logmsg)
        except Exception as e:
            print('logPoster error:', str(e))
            traceback.print_exc()