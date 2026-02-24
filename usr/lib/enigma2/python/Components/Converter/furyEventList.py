# -*- coding: utf-8 -*-
#
# EventList - Converter
# modification by ISLAM SALAMA (( SKIN FURY ))

# Coded by Dr.Best (c) 2013
# Support: www.dreambox-tools.info
# E-Mail: dr.best@dreambox-tools.info
#
# This plugin is open source but it is NOT free software.
#
# This plugin may only be distributed to and executed on hardware which
# is licensed by Dream Property GmbH.
# In other words:
# It's NOT allowed to distribute any parts of this plugin or its source code in ANY way
# to hardware which is NOT licensed by Dream Property GmbH.
# It's NOT allowed to execute this plugin and its source code or even parts of it in ANY way
# on hardware which is NOT licensed by Dream Property GmbH.
#
# If you want to use or modify the code or parts of it,
# you have to keep MY license and inform me about the modifications by mail.
#
# <widget source="ServiceEvent" render="EventListDisplay" position="1080,610" size="1070,180"
#     column0="0,100,yellow,Regular,30,0,0" column1="100,950,white,Regular,28,0,1"
#     primetimeoffset="0" rowHeight="35" backgroundColor="#FF101010" transparent="1" zPosition="50">
#     <convert type="furyEventList">beginOnly=yes,primetime=yes,eventcount=4</convert>
# </widget>

from Components.Converter.Converter import Converter
from Components.Converter.Poll import Poll
from Components.Element import cached
from enigma import eEPGCache, eServiceReference
from time import localtime, strftime, mktime, time as time_time
from datetime import datetime, timedelta

try:
    from Components.config import config
    from Plugins.Extensions.AIFury.plugin import AIFuryController
except Exception:
    config = None
    AIFuryController = None


def _is_translate_allowed():
    """Return True only when plugin and translation feature are enabled."""
    try:
        if config is None:
            return False
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enabled"):
            if not config.plugins.aifury.enabled.value:
                return False
        if hasattr(config.plugins, "aifury") and hasattr(config.plugins.aifury, "enable_translate_current_event"):
            if not config.plugins.aifury.enable_translate_current_event.value:
                return False
        return True
    except Exception:
        return False



def is_arabic(text):
    """
    فحص بسيط: لو في أي حرف من اليونيكود العربي نعتبر النص عربي.
    ده يخلي العناوين العربية تفضل زي ما هي بدون ترجمة.
    """
    if not text:
        return False
    for ch in text:
        # Arabic, Arabic Supplement, Arabic Extended-A (تبسيط كافي للـ EPG)
        if u'\u0600' <= ch <= u'\u06FF' or \
           u'\u0750' <= ch <= u'\u077F' or \
           u'\u08A0' <= ch <= u'\u08FF':
            return True
    return False


def fury_translate(text):
    """
    ترجمة نص باستخدام AIFuryController (لو متاح ومفعّل)،
    مع الاعتماد على الكاش وعدم حبس الـ GUI.

    - لو النص عربي: نسيبه زي ما هو.
    - لو النص بأي لغة تانية: نبعته للترجمة (يفضّل تضبط لغة FuryEPG على English).

    ملاحظة مهمة:
        من هنا ما نقدرش نغيّر لغة الترجمة الحقيقية للـ FuryEPG،
        هي بتتحدد من إعدادات البلجن (config.plugins.aifury.language).
        الكود ده بس بيقرر "أترجم ولا لأ" + يعتمد على نفس الكاش.
    """
    if not text:
        return text

    # لو العنوان عربي، سيبه زي ما هو
    if is_arabic(text):
        return text


    # لو البلجن أو الترجمة مقفولين: رجّع النص الأصلي فوراً
    if not _is_translate_allowed():
        return text

    try:
        if AIFuryController is None:
            return text
        ctrl = AIFuryController.instance
    except Exception:
        return text

    if ctrl is None or not getattr(ctrl, "enabled", False):
        return text

    # نقرأ اللغة من إعدادات FuryEPG (يفضّل تبقى "en" لو عايز إنجليزي)
    lang = "en"
    if config is not None:
        try:
            lang = config.plugins.aifury.language.value
        except Exception:
            pass

    cache = getattr(ctrl, "cache", None)
    if not isinstance(cache, dict):
        cache = None

    cache_key = "%s|%s" % (lang, text)

    translated = None
    if cache is not None:
        try:
            translated = cache.get(cache_key, None)
        except Exception:
            translated = None

    # لو مفيش ترجمة في الكاش: شغّل الترجمة في الخلفية وارجّع النص الأصلي مؤقتًا
    if not translated:
        # Throttle: ما نكررش نفس الطلب كل ريفريش/بول (يمنع ضغط على البلجن/الشبكة)
        now_ts = time_time()
        last = _translate_last_req.get(cache_key, 0)
        if (now_ts - last) >= _TRANSLATE_THROTTLE_SEC:
            _translate_last_req[cache_key] = now_ts
            try:
                ctrl.translate_async(text)
            except Exception as e:
                print("[furyEventList] translate_async error:", e)
        return text

    return translated or text


# ===== ترجمة: منع تكرار طلبات الترجمة لنفس النص بسرعة (Throttle) =====
_translate_last_req = {}
_TRANSLATE_THROTTLE_SEC = 10


class furyEventList(Poll, Converter, object):
    def __init__(self, type):
        Converter.__init__(self, type)
        Poll.__init__(self)
        # ريفريش دوري علشان الترجمة تظهر تلقائي بدون تغيير القناة
        # (تقدر تغيّرها: كل كام ملي ثانية يحصل تحديث)
        self.poll_interval = 2000  # ms
        self.poll_enabled = True
        self.epgcache = eEPGCache.getInstance()
        self.primetime = 0
        self.eventcount = 0
        self.beginOnly = False

        if type:
            args = type.split(',')
            for arg in args:
                try:
                    key, value = arg.split('=')
                except ValueError:
                    continue
                if key == "eventcount":
                    try:
                        self.eventcount = int(value)
                    except Exception:
                        self.eventcount = 0
                elif key == "primetime":
                    if value == "yes":
                        self.primetime = 1
                elif key == "beginOnly":
                    if value == "yes":
                        self.beginOnly = True

    @cached
    def getContent(self):
        contentList = []

        ref = self.source.service
        info = ref and self.source.info
        if info is None:
            return []

        try:
            event = self.source.getCurrentEvent()
        except Exception:
            event = None

        if not event:
            return contentList

        # الأحداث اللي بعد الحدث الحالي
        i = 1
        while i <= self.eventcount and event:
            try:
                next_begin = event.getBeginTime() + event.getDuration()
                event = self.epgcache.lookupEventTime(
                    eServiceReference(ref.toString()),
                    next_begin
                )
            except Exception:
                event = None

            if event:
                contentList.append(self.getEventTuple(event))
            i += 1

        # حدث الـ PrimeTime لو مفعّل
        if self.primetime == 1:
            now = localtime(time_time())
            dt = datetime(now.tm_year, now.tm_mon, now.tm_mday, 20, 15)
            if time_time() > mktime(dt.timetuple()):
                dt += timedelta(days=1)  # skip to next day...
            primeTime = int(mktime(dt.timetuple()))
            try:
                pevent = self.epgcache.lookupEventTime(
                    eServiceReference(ref.toString()),
                    primeTime
                )
            except Exception:
                pevent = None

            if pevent and (pevent.getBeginTime() <= primeTime):
                contentList.append(self.getEventTuple(pevent))

        return contentList

    def getEventTuple(self, event):
        try:
            if self.beginOnly:
                t_str = "%s" % (strftime("%H:%M", localtime(event.getBeginTime())),)
            else:
                t_str = "%s - %s" % (
                    strftime("%H:%M", localtime(event.getBeginTime())),
                    strftime("%H:%M", localtime(event.getBeginTime() + event.getDuration()))
                )

            # عنوان الحدث + ترجمة حسب الشرط (لو مش عربي)
            title = event.getEventName() or ""
            title_t = fury_translate(title)

            duration = "%d min" % (event.getDuration() / 60)

            return (t_str, title_t, duration)
        except Exception as e:
            print("[furyEventList] Error GetEventTuple converter furyEventList:", e)
            return ("", "", "")

    def changed(self, what):
        if what[0] != self.CHANGED_SPECIFIC:
            Converter.changed(self, what)