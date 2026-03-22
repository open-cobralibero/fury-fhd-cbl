# -*- coding: utf-8 -*-
# Programming and modification by ISLAM SALAMA (( SKIN FURY ))
# Optimized for safer polling, cleaner Arabic output, and lower UI load.

from Components.Converter.Converter import Converter
from Components.Element import cached
from enigma import eEPGCache, eTimer

import re
import time
import unicodedata

# FuryEpg settings + controller (if installed)
try:
    from Components.config import config
    from Plugins.Extensions.AIFury.plugin import AIFuryController
except Exception:
    config = None
    AIFuryController = None

try:
    text_type = unicode
    string_types = (basestring,)
except NameError:
    text_type = str
    string_types = (str, bytes)

# ------------- shared helpers -------------
# key -> last request timestamp
_FURY_REQUESTED = {}
_REQUEST_RETRY_SECONDS = 8.0
_MAX_TRACKED_REQUESTS = 1024

_ARABIC_RE = re.compile(u"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_ALPHA_RE = re.compile(u"[^\\W\\d_]", re.UNICODE)
_ZERO_WIDTH_RE = re.compile(u"[\u200b\u200c\u200d\ufeff]")
_SPACES_RE = re.compile(u"[ \t\f\v]+")
_SPACES_AROUND_NL_RE = re.compile(u"[ \t]*\n[ \t]*")
_MULTI_NL_RE = re.compile(u"\n{3,}")
_AR_PUNCT_FIX_RE = re.compile(u"\\s+([،؛:!؟\\.,\\)\\]\\}])")
_OPEN_PUNCT_FIX_RE = re.compile(u"([\\(\\[\\{])\\s+")
_DOTS_RE = re.compile(u"\\.{4,}")
_BAD_OUTPUT_RE = re.compile(u"[\ufffd]")
_BIDI_MARKS_RE = re.compile(u"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_RTL_EMBED = u"\u202B"
_LTR_EMBED = u"\u202A"
_POP_DIRECTION = u"\u202C"



def _strip_bidi_marks(text):
    return _BIDI_MARKS_RE.sub(u"", _u(text) or u"")



def _auto_direction_line(text):
    text = _strip_bidi_marks(text)
    if not text:
        return u""

    rtl = 0
    ltr = 0
    for ch in text:
        try:
            bidi = unicodedata.bidirectional(ch)
        except Exception:
            bidi = u""
        if bidi in (u"R", u"AL"):
            rtl += 1
        elif bidi == u"L":
            ltr += 1

    if _contains_arabic(text) or (rtl and rtl >= ltr):
        return _RTL_EMBED + text + _POP_DIRECTION
    if ltr:
        return _LTR_EMBED + text + _POP_DIRECTION
    return text



def _auto_direction_text(text):
    text = _u(text)
    if not text:
        return u""
    return u"\n".join(_auto_direction_line(line) if line else u"" for line in text.split(u"\n"))


def _now():
    try:
        return time.time()
    except Exception:
        return 0.0



def _u(value):
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        for enc in ("utf-8", "utf-16", "cp1256", "latin-1"):
            try:
                return value.decode(enc)
            except Exception:
                pass
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(str(value))
        except Exception:
            return u""



def _normalize_text(text):
    text = _u(text)
    if not text:
        return u""

    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass

    text = text.replace(u"\r\n", u"\n").replace(u"\r", u"\n")
    text = _ZERO_WIDTH_RE.sub(u"", text)
    text = _SPACES_AROUND_NL_RE.sub(u"\n", text)
    text = _SPACES_RE.sub(u" ", text)
    text = _MULTI_NL_RE.sub(u"\n\n", text)
    return text.strip()



def _contains_arabic(text):
    return bool(_ARABIC_RE.search(_u(text) or u""))



def _is_mostly_arabic(text):
    text = _u(text)
    if not text:
        return False
    ar = len(_ARABIC_RE.findall(text))
    if ar == 0:
        return False

    latin = 0
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90 or 97 <= o <= 122:
            latin += 1
    return ar >= max(2, latin * 2)



def _has_letters(text):
    return bool(_ALPHA_RE.search(_u(text) or u""))



def _looks_sentence(text):
    text = _normalize_text(text)
    if not text:
        return False
    if u"\n" in text:
        return True
    if len(text) >= 24:
        return True
    words = [w for w in text.split(u" ") if w]
    if len(words) >= 4:
        return True
    for ch in (u".", u":", u";", u"؛", u"!", u"؟", u"?"):
        if ch in text:
            return True
    return False



def _cleanup_arabic_text(text):
    text = _normalize_text(text)
    if not text:
        return u""

    if _contains_arabic(text):
        text = text.replace(u"?", u"؟")
        text = text.replace(u";", u"؛")
        text = text.replace(u",", u"،")

    text = _AR_PUNCT_FIX_RE.sub(u"\\1", text)
    text = _OPEN_PUNCT_FIX_RE.sub(u"\\1", text)
    text = _DOTS_RE.sub(u"...", text)
    text = _MULTI_NL_RE.sub(u"\n\n", text)
    return text.strip()



def _join_nonempty(parts):
    out = []
    for part in parts:
        part = _normalize_text(part)
        if part and (not out or out[-1] != part):
            out.append(part)
    return u"\n".join(out).strip()



def _get_lang():
    try:
        return _normalize_text(config.plugins.aifury.language.value) or u"ar"
    except Exception:
        return u"ar"



def _is_plugin_enabled():
    """Master plugin enabled switch."""
    try:
        if config is None:
            return False
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enabled"):
            return bool(config.plugins.aifury.enabled.value)
        return True
    except Exception:
        return False



def _is_infobar_name_translate_enabled():
    """Controls translating the event NAME shown in InfoBar (EpgFuryEvent type=Name)."""
    try:
        if not _is_plugin_enabled():
            return False
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "infobar_translate"):
            return bool(config.plugins.aifury.infobar_translate.value)
        # Backwards-compat: older plugin versions only had one switch
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enable_translate_current_event"):
            return bool(config.plugins.aifury.enable_translate_current_event.value)
        return True
    except Exception:
        return False



def _is_description_translate_enabled():
    """Controls translating descriptions (Description / Full) independently from InfoBar name."""
    try:
        if not _is_plugin_enabled():
            return False
        # Descriptions follow the main current-event translation switch
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enable_translate_current_event"):
            return bool(config.plugins.aifury.enable_translate_current_event.value)
        return True
    except Exception:
        return False



def _get_controller():
    """Return controller if plugin is enabled (feature toggles are handled per-field)."""
    if not _is_plugin_enabled():
        return None

    ctrl = None
    try:
        if AIFuryController is not None:
            ctrl = getattr(AIFuryController, "instance", None)
            if callable(ctrl):
                ctrl = ctrl()
    except Exception:
        ctrl = None
    return ctrl



def _prune_request_registry():
    try:
        if len(_FURY_REQUESTED) <= _MAX_TRACKED_REQUESTS:
            return
        ordered = sorted(_FURY_REQUESTED.items(), key=lambda item: item[1])
        cut = int(len(ordered) * 0.30) or 1
        for key, _stamp in ordered[:cut]:
            _FURY_REQUESTED.pop(key, None)
    except Exception:
        pass



def _cache_key(text):
    text = _normalize_text(text)
    if not text:
        return None
    return u"%s|%s" % (_get_lang(), text)



def _source_from_key(key):
    key = _u(key)
    if not key:
        return u""
    if u"|" in key:
        return key.split(u"|", 1)[1]
    return key



def _needs_translation(text):
    text = _normalize_text(text)
    if not text:
        return False

    if not _has_letters(text):
        return False

    target_lang = _get_lang().lower()
    if target_lang == u"ar" and _is_mostly_arabic(text):
        return False
    return True



def _sanitize_translation(src_text, translated_text):
    src_text = _normalize_text(src_text)
    translated_text = _normalize_text(translated_text)

    if not translated_text:
        return None
    if _BAD_OUTPUT_RE.search(translated_text):
        return None

    target_lang = _get_lang().lower()

    if target_lang == u"ar":
        translated_text = _cleanup_arabic_text(translated_text)

        if src_text and translated_text == src_text and _looks_sentence(src_text) and not _contains_arabic(src_text):
            return None

        if src_text and not _contains_arabic(src_text) and _looks_sentence(src_text) and not _contains_arabic(translated_text):
            return None

    return translated_text



def _cache_get(ctrl, key, src_text=None):
    if ctrl is None or not key:
        return None

    try:
        value = ctrl.cache.get(key, None)
    except Exception:
        return None

    clean = _sanitize_translation(src_text or _source_from_key(key), value)
    if clean is None:
        return None

    try:
        if value != clean:
            ctrl.cache[key] = clean
    except Exception:
        pass

    _FURY_REQUESTED.pop(key, None)
    return clean



def _can_request(key):
    stamp = _FURY_REQUESTED.get(key)
    if stamp is None:
        return True
    return (_now() - stamp) >= _REQUEST_RETRY_SECONDS



def _request_translation(ctrl, text, key=None):
    text = _normalize_text(text)
    if not text:
        return False
    if ctrl is None or not hasattr(ctrl, "translate_async"):
        return False
    if not _needs_translation(text):
        return False

    key = key or _cache_key(text)
    if not key:
        return False
    if not _can_request(key):
        return False

    try:
        _FURY_REQUESTED[key] = _now()
        _prune_request_registry()
        ctrl.translate_async(text)
        return True
    except Exception as e:
        print("[EpgFuryEvent] translate_async error:", e)
        return False



def _translate_or_request(text):
    """Return translated text if in cache; otherwise request async translate and return original."""
    text = _normalize_text(text)
    if not text:
        return text
    if not _needs_translation(text):
        return text

    ctrl = _get_controller()
    if ctrl is None:
        return text

    key = _cache_key(text)
    if not key:
        return text

    translated = _cache_get(ctrl, key, text)
    if translated is not None:
        return translated

    if _request_translation(ctrl, text, key):
        translated = _cache_get(ctrl, key, text)
        if translated is not None:
            return translated
    return text



def _translate_block(text, allow_translate=True, ctrl=None):
    """
    Translate a block safely.
    Returns: (best_text_for_now, waiting_keys)
    """
    text = _normalize_text(text)
    if not text:
        return u"", set()

    if not allow_translate or not _needs_translation(text):
        return text, set()

    if ctrl is None:
        ctrl = _get_controller()
    if ctrl is None:
        return text, set()

    # Keep logical lines separate to improve cache hit rate and translation quality.
    lines = [line for line in text.split(u"\n") if _normalize_text(line)]
    if not lines:
        lines = [text]

    out_lines = []
    waiting = set()

    for line in lines:
        line = _normalize_text(line)
        if not line:
            continue
        if not _needs_translation(line):
            out_lines.append(line)
            continue

        key = _cache_key(line)
        translated = _cache_get(ctrl, key, line)
        if translated is None:
            if _request_translation(ctrl, line, key):
                translated = _cache_get(ctrl, key, line)
            if translated is None:
                waiting.add(key)
                out_lines.append(line)
            else:
                out_lines.append(translated)
        else:
            out_lines.append(translated)

    result = _join_nonempty(out_lines)
    if not result:
        result = text
    return result, waiting


# ================= Converter =================
class EpgFuryEvent(Converter, object):
    NAME = 0
    DESCRIPTION = 1
    FULL = 2  # FullDescription

    # Fast first refreshes, then slower to avoid UI load.
    _poll_fast_interval = 250
    _poll_slow_interval = 700
    _poll_fast_tries = 4
    _poll_max_tries = 14

    def __init__(self, type):
        Converter.__init__(self, type)
        t = (type or "").strip()
        if t == "Name":
            self.type = self.NAME
        elif t == "Description":
            self.type = self.DESCRIPTION
        else:
            self.type = self.FULL

        self._timer = eTimer()
        try:
            self._timer.callback.append(self._onTimer)
        except Exception:
            try:
                self._timer_conn = self._timer.timeout.connect(self._onTimer)
            except Exception:
                self._timer_conn = None

        self._poll_tries = 0
        self._polling_keys = set()

    def _next_poll_interval(self):
        if self._poll_tries < self._poll_fast_tries:
            return self._poll_fast_interval
        return self._poll_slow_interval

    def _start_timer(self, reset=False):
        if reset:
            self._poll_tries = 0

        interval = self._next_poll_interval()
        try:
            self._timer.start(interval, False)
            return
        except Exception:
            pass
        try:
            self._timer.start(interval)
        except Exception:
            pass

    def _stop_timer(self):
        try:
            if self._timer.isActive():
                self._timer.stop()
                return
        except Exception:
            pass
        try:
            self._timer.stop()
        except Exception:
            pass

    def _invalidate(self):
        try:
            self.changed((self.CHANGED_POLL,))
        except Exception:
            Converter.changed(self, (self.CHANGED_POLL,))

    def _set_waiting_keys(self, keys):
        keys = set([k for k in keys if k])
        if not keys:
            self._polling_keys.clear()
            self._stop_timer()
            return

        if keys != self._polling_keys:
            self._polling_keys = keys
            self._start_timer(reset=True)
        else:
            try:
                active = self._timer.isActive()
            except Exception:
                active = False
            if not active:
                self._start_timer(reset=False)

    def _onTimer(self):
        ctrl = _get_controller()
        if ctrl is None or not self._polling_keys:
            self._stop_timer()
            return

        resolved_any = False
        remaining = set()
        for key in list(self._polling_keys):
            if _cache_get(ctrl, key) is None:
                remaining.add(key)
            else:
                resolved_any = True

        self._polling_keys = remaining
        self._poll_tries += 1

        if resolved_any:
            self._invalidate()

        if not self._polling_keys or self._poll_tries >= self._poll_max_tries:
            self._polling_keys.clear()
            self._stop_timer()
            if not resolved_any:
                self._invalidate()
            return

        self._start_timer(reset=False)

    def getText(self):
        event = getattr(self.source, "event", None)

        if event is None:
            service = getattr(self.source, "service", None)
            if service is not None:
                try:
                    epgcache = eEPGCache.getInstance()
                    event = epgcache.lookupEventTime(service, -1)
                except Exception:
                    event = None

        if event is None:
            return ""

        name = _normalize_text(event.getEventName() or "")
        short = _normalize_text(event.getShortDescription() or "")
        ext = _normalize_text(event.getExtendedDescription() or "")

        if short and ext and short == ext:
            ext = u""

        if not (name or short or ext):
            return ""

        allow_name = _is_infobar_name_translate_enabled()
        allow_desc = _is_description_translate_enabled()

        if not allow_name and not allow_desc:
            if self.type == self.NAME:
                return _auto_direction_text(name)
            if self.type == self.DESCRIPTION:
                return _auto_direction_text(_join_nonempty([short, ext]) or name)
            return _auto_direction_text(_join_nonempty([name, short, ext]))

        ctrl = _get_controller()

        name_t, wait_name = _translate_block(name, allow_name, ctrl)
        short_t, wait_short = _translate_block(short, allow_desc, ctrl)
        ext_t, wait_ext = _translate_block(ext, allow_desc, ctrl)

        waiting = set()
        waiting.update(wait_name)
        waiting.update(wait_short)
        waiting.update(wait_ext)
        self._set_waiting_keys(waiting)

        if self.type == self.NAME:
            return _auto_direction_text(name_t or name)

        if self.type == self.DESCRIPTION:
            desc = _join_nonempty([short_t, ext_t])
            return _auto_direction_text(desc or name_t or name)

        full = _join_nonempty([name_t, short_t, ext_t])
        if not full:
            full = _join_nonempty([name, short, ext])
        return _auto_direction_text(full)

    text = property(getText)

    def changed(self, what):
        Converter.changed(self, what)


# ================= Patch EPGList (event names in the LIST) =================
def _patch_epglist():
    """Patch Components.EpgList.EPGList build entry functions so event names use translated cache
    and trigger periodic invalidate to refresh while standing on the same item."""
    try:
        from Components.EpgList import EPGList
    except Exception:
        return

    # Avoid double patching
    if getattr(EPGList, "_furyepg_patched", False):
        return
    EPGList._furyepg_patched = True

    def _ensure_timer(self):
        if hasattr(self, "_furyepg_timer"):
            return
        self._furyepg_timer = eTimer()
        self._furyepg_left = 0

        def _tick():
            try:
                if hasattr(self, "l") and self.l is not None:
                    try:
                        self.l.invalidate()
                    except Exception:
                        pass
            except Exception:
                pass

            self._furyepg_left -= 1
            if self._furyepg_left > 0:
                try:
                    self._furyepg_timer.start(500, False)
                except Exception:
                    try:
                        self._furyepg_timer.start(500)
                    except Exception:
                        pass
            else:
                try:
                    self._furyepg_timer.stop()
                except Exception:
                    pass

        try:
            self._furyepg_timer.callback.append(_tick)
        except Exception:
            try:
                self._furyepg_timer.timeout.connect(_tick)
            except Exception:
                pass

    def _kick_redraw(self):
        _ensure_timer(self)
        self._furyepg_left = 12  # ~6 seconds, enough to pick up async cache without heavy redraws
        try:
            self._furyepg_timer.start(500, False)
        except Exception:
            try:
                self._furyepg_timer.start(500)
            except Exception:
                pass

    def _find_event_from_args(args):
        for a in args:
            if hasattr(a, "getEventName") and callable(getattr(a, "getEventName")):
                try:
                    return a
                except Exception:
                    pass

        try:
            epgcache = eEPGCache.getInstance()
        except Exception:
            epgcache = None
        if epgcache is None:
            return None

        service = None
        event_id = None
        for a in args:
            if service is None and hasattr(a, "toString") and callable(getattr(a, "toString")):
                service = a
            if event_id is None and isinstance(a, int) and a > 0:
                event_id = a

        if service is not None and event_id is not None and hasattr(epgcache, "lookupEventId"):
            try:
                return epgcache.lookupEventId(service, event_id)
            except Exception:
                return None
        return None

    def _replace_text(obj, old, new):
        if old is None or new is None or old == new:
            return obj
        if isinstance(obj, list):
            return [_replace_text(x, old, new) for x in obj]
        if isinstance(obj, tuple) and obj:
            try:
                if isinstance(obj[-1], string_types) and obj[-1] == old:
                    return obj[:-1] + (new,)
            except Exception:
                pass
            return tuple(_replace_text(x, old, new) for x in obj)
        return obj

    def _wrap_builder(fn_name):
        orig = getattr(EPGList, fn_name, None)
        if orig is None or not callable(orig):
            return

        def _patched(self, *args, **kwargs):
            entry = orig(self, *args, **kwargs)

            ev = _find_event_from_args(args) or _find_event_from_args(kwargs.values())
            if ev is None:
                return entry

            try:
                raw_name = ev.getEventName() or ""
            except Exception:
                raw_name = ""

            clean_name = _normalize_text(raw_name)
            if not clean_name:
                return entry

            display_name = _auto_direction_text(clean_name)

            if not _is_infobar_name_translate_enabled() or not _needs_translation(clean_name):
                if display_name == raw_name:
                    return entry
                try:
                    return _replace_text(entry, raw_name, display_name)
                except Exception:
                    return entry

            ctrl = _get_controller()
            key = _cache_key(clean_name)
            tr = _cache_get(ctrl, key, clean_name) if (ctrl and key) else None

            if tr is None:
                if _request_translation(ctrl, clean_name, key):
                    tr = _cache_get(ctrl, key, clean_name)
                if tr is None:
                    try:
                        _kick_redraw(self)
                    except Exception:
                        pass
                    return entry

            try:
                return _replace_text(entry, raw_name, _auto_direction_text(tr))
            except Exception:
                return entry

        setattr(EPGList, fn_name, _patched)

    _wrap_builder("buildSingleEntry")
    _wrap_builder("buildMultiEntry")
    _wrap_builder("buildEventEntry")


try:
    _patch_epglist()
except Exception as e:
    print("[EpgFuryEvent] EPGList patch error:", e)
