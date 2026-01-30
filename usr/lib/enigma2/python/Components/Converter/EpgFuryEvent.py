# -*- coding: utf-8 -*-
# Programming and modification by ISLAM SALAMA (( SKIN FURY ))

from Components.Converter.Converter import Converter
from Components.Element import cached
from enigma import eEPGCache, eTimer

# FuryEpg settings + controller (if installed)
try:
    from Components.config import config
    from Plugins.Extensions.AIFury.plugin import AIFuryController
except Exception:
    config = None
    AIFuryController = None

# ------------- shared helpers -------------
_FURY_REQUESTED = set()  # avoid spamming translate_async


def _get_lang():
    try:
        return config.plugins.aifury.language.value
    except Exception:
        return "ar"


def _is_translate_allowed():
    """Return True only when plugin and translation feature are enabled."""
    try:
        if config is None:
            return False
        # plugin master switch
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enabled"):
            if not config.plugins.aifury.enabled.value:
                return False
        # per-feature switch (Enable translate)
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enable_translate_current_event"):
            if not config.plugins.aifury.enable_translate_current_event.value:
                return False
        return True
    except Exception:
        return False



def _get_controller():
    if not _is_translate_allowed():
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


def _cache_key(text):
    if not text:
        return None
    return "%s|%s" % (_get_lang(), text)


def _cache_get(ctrl, key):
    try:
        return ctrl.cache.get(key, None)
    except Exception:
        return None


def _translate_or_request(text):
    """Return translated text if in cache; otherwise request async translate and return original."""
    if not text:
        return text
    ctrl = _get_controller()
    if ctrl is None:
        return text
    k = _cache_key(text)
    if not k:
        return text
    t = _cache_get(ctrl, k)
    if t is not None:
        return t
    # not translated yet -> request
    if hasattr(ctrl, "translate_async"):
        try:
            if k not in _FURY_REQUESTED:
                _FURY_REQUESTED.add(k)
                ctrl.translate_async(text)
        except Exception as e:
            print("[EpgFuryEvent] translate_async error:", e)
    return text


# ================= Converter =================
class EpgFuryEvent(Converter, object):
    NAME = 0
    DESCRIPTION = 1
    FULL = 2  # FullDescription

    # How often to poll cache while waiting (ms)
    _poll_interval = 500
    _poll_max_tries = 20  # poll_interval * tries

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

    def _start_timer(self):
        try:
            if not self._timer.isActive():
                self._poll_tries = 0
                self._timer.start(self._poll_interval, False)
                return
        except Exception:
            pass
        try:
            self._poll_tries = 0
            self._timer.start(self._poll_interval)
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

    def _onTimer(self):
        ctrl = _get_controller()
        if ctrl is None or not self._polling_keys:
            self._stop_timer()
            return

        done = True
        for k in list(self._polling_keys):
            if _cache_get(ctrl, k) is None:
                done = False
                break

        self._poll_tries += 1

        if done or self._poll_tries >= self._poll_max_tries:
            self._polling_keys.clear()
            self._stop_timer()
            # invalidate converter cache so skin refreshes while standing
            try:
                self.changed((self.CHANGED_POLL,))
            except Exception:
                Converter.changed(self, (self.CHANGED_POLL,))
            return

        self._start_timer()

    @cached
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

        name = event.getEventName() or ""
        short = event.getShortDescription() or ""
        ext = event.getExtendedDescription() or ""

        if not (name or short or ext):
            return ""

        desc_parts = [p for p in (short, ext) if p]
        desc_text = "\n".join(desc_parts).strip()

        # ## FURY: no-translate early return
        if not _is_translate_allowed():
            if self.type == self.NAME:
                return name
            desc_parts = [p for p in (short, ext) if p]
            desc_text = "\n".join(desc_parts).strip()
            if self.type == self.DESCRIPTION:
                return desc_text or name
            parts = []
            if name:
                parts.append(name)
            if desc_text:
                parts.append(desc_text)
            return "\n".join(parts).strip()

        ctrl = _get_controller()
        name_key = _cache_key(name) if name else None
        desc_key = _cache_key(desc_text) if desc_text else None

        name_t = _cache_get(ctrl, name_key) if (ctrl and name_key) else None
        desc_t = _cache_get(ctrl, desc_key) if (ctrl and desc_key) else None

        need_poll = False
        if ctrl is not None and hasattr(ctrl, "translate_async"):
            try:
                if name and name_t is None and name_key and name_key not in _FURY_REQUESTED:
                    _FURY_REQUESTED.add(name_key)
                    ctrl.translate_async(name)
                    need_poll = True
                if desc_text and desc_t is None and desc_key and desc_key not in _FURY_REQUESTED:
                    _FURY_REQUESTED.add(desc_key)
                    ctrl.translate_async(desc_text)
                    need_poll = True
            except Exception as e:
                print("[EpgFuryEvent] translate_async error:", e)

        if need_poll:
            self._polling_keys = set([k for k in (name_key, desc_key) if k])
            self._start_timer()
        else:
            self._polling_keys.clear()
            self._stop_timer()

        if name_t is None:
            name_t = name
        if desc_t is None:
            desc_t = desc_text

        if self.type == self.NAME:
            return name_t or name

        if self.type == self.DESCRIPTION:
            return desc_t or name_t or name

        parts = []
        if name_t:
            parts.append(name_t)
        if desc_t:
            parts.append(desc_t)
        full = "\n".join(parts)
        if not full:
            full = name or desc_text
        return full

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
        self._furyepg_left = 20  # ~10 seconds
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
                if isinstance(obj[-1], str) and obj[-1] == old:
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
                name = ev.getEventName() or ""
            except Exception:
                name = ""

            if not name:
                return entry

            ctrl = _get_controller()
            k = _cache_key(name)
            tr = _cache_get(ctrl, k) if (ctrl and k) else None

            if tr is None:
                _translate_or_request(name)
                try:
                    _kick_redraw(self)
                except Exception:
                    pass
                return entry

            try:
                return _replace_text(entry, name, tr)
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
