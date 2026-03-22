# based on version by areq 2015-12-13 http://areq.eu.org/
# mod by Fhroma version 12.10.2018
# improved version
# mod by Islam Salama V: 1/1/2025
# fixed by OpenAI: 2026-03-13
from __future__ import absolute_import
from enigma import (
    eConsoleAppContainer,
    eTimer,
    iServiceInformation,
)
from Components.Console import Console
from Components.Converter.Converter import Converter
from Components.Element import cached
import re
import six
from datetime import datetime
from os import path

DBG = False
DEBUG_FILE = '/tmp/furyComponents.log'
BITRATE_CANDIDATES = (
    '/usr/bin/bitrate',
    '/usr/sbin/bitrate',
)

# Keep old behaviour by default for skin compatibility.
# Change to 'total' if you want the displayed value to be video+audio.
DEFAULT_MODE = 'video'

# Cached image type
_image_type = None
_append_to_file = False
_int_re = re.compile(r'-?\d+')


def AGDEBUG(my_text=None, append=True, debug_file=DEBUG_FILE):
    global _append_to_file
    if not debug_file or not my_text:
        return

    try:
        mode = 'a' if _append_to_file and append else 'w'
        _append_to_file = True

        with open(debug_file, mode) as f:
            f.write('{}\t{}\n'.format(datetime.now(), my_text))

        # Rotate log if too big
        if path.getsize(debug_file) > 100000:
            with open(debug_file, 'r+') as f:
                lines = f.readlines()
                f.seek(0)
                f.writelines(lines[10:])
                f.truncate()
    except Exception as e:
        try:
            with open(debug_file, 'a') as f:
                f.write('Exception: {}\n'.format(e))
        except Exception:
            pass


def isImageType(img_name=''):
    global _image_type

    if _image_type is None:
        feed_conf = '/etc/opkg/all-feed.conf'
        if path.exists(feed_conf):
            try:
                with open(feed_conf, 'r') as f:
                    content = f.read().lower()
                    if 'vti' in content:
                        _image_type = 'vti'
                    elif 'code.vuplus.com' in content:
                        _image_type = 'vuplus'
                    elif 'openpli-7' in content:
                        _image_type = 'openpli7'
                    elif 'openatv' in content:
                        _image_type = 'openatv'
                        if '/5.3/' in content:
                            _image_type += '5.3'
            except Exception:
                pass

        if _image_type is None:
            if path.exists('/usr/lib/enigma2/python/Plugins/SystemPlugins/VTIPanel/'):
                _image_type = 'vti'
            elif path.exists('/usr/lib/enigma2/python/Plugins/Extensions/Infopanel/'):
                _image_type = 'openatv'
            elif path.exists('/usr/lib/enigma2/python/Blackhole'):
                _image_type = 'blackhole'
            elif path.exists('/etc/init.d/start_pkt.sh'):
                _image_type = 'pkt'
            else:
                _image_type = 'unknown'

    return img_name.lower() == _image_type.lower()


def find_bitrate_binary():
    for candidate in BITRATE_CANDIDATES:
        if path.exists(candidate):
            return candidate
    return None


class furyBitrate(Converter, object):
    def __init__(self, type):
        Converter.__init__(self, type)
        self.display_type = (type or '').strip().lower() or DEFAULT_MODE
        self.bitrate_bin = find_bitrate_binary()
        self.clear_values()

        self.is_running = False
        self.is_suspended = False
        self.my_console = Console()
        self.container = eConsoleAppContainer()
        self.container.appClosed.append(self.app_closed)
        self.container.dataAvail.append(self.data_avail)

        self.start_timer = eTimer()
        self.start_timer.callback.append(self.start)
        self.start_timer.start(100, True)

        self.run_timer = eTimer()
        self.run_timer.callback.append(self.run_bitrate)

        self.probe_timer = eTimer()
        self.probe_timer.callback.append(self.probe_timeout)

        self.command_variants = []
        self.variant_index = 0
        self.last_working_variant = None
        self.parsed_this_run = False
        self.current_apid = 0
        self.pending_stats = []
        self.last_error = ''

        if self.bitrate_bin:
            self.my_console.ePopen('chmod 755 {}'.format(self.bitrate_bin))
        elif DBG:
            AGDEBUG('[furyBitrate:init] bitrate binary not found')

    @cached
    def getText(self):
        if DBG:
            AGDEBUG('[furyBitrate:getText] vcur {} acur {}'.format(self.vcur, self.acur))

        vcur = self._sanitize_kbps(self.vcur)
        acur = self._sanitize_kbps(self.acur)
        total = vcur + acur

        if self.display_type in ('audio', 'audiobitrate', 'abitrate'):
            value = acur
        elif self.display_type in ('total', 'sum', 'all', 'stream', 'full'):
            value = total
        else:
            value = vcur

        return self._format_mbps(value)

    text = property(getText)

    def _sanitize_kbps(self, value):
        try:
            value = int(value)
        except Exception:
            return 0
        return value if 0 <= value < 1000000 else 0

    def _format_mbps(self, kbps):
        # Bitrate units are usually decimal in broadcast/networking.
        mbps = float(kbps) / 1000.0
        return '{:.2f} Mb/s'.format(mbps)

    def _reset_parser_state(self):
        self.remaining_data = ''
        self.data_lines = []
        self.pending_stats = []
        self.parsed_this_run = False
        self.last_error = ''

    def clear_values(self, *args):
        if DBG:
            AGDEBUG('[furyBitrate:clear_values] >>>')

        self.is_running = False
        self.vmin = self.vmax = self.vavg = self.vcur = 0
        self.amin = self.amax = self.aavg = self.acur = 0
        self.remaining_data = ''
        self.data_lines = []
        self.pending_stats = []
        self.parsed_this_run = False
        self.current_apid = 0
        self.last_error = ''
        Converter.changed(self, (self.CHANGED_POLL,))

    def doSuspend(self, suspended):
        if DBG:
            AGDEBUG('[furyBitrate:suspended] >>> self.is_suspended={}, suspended={}'.format(self.is_suspended, suspended))

        if not suspended:
            self.is_suspended = False
            self.start_timer.start(100, True)
        else:
            self.start_timer.stop()
            self.run_timer.stop()
            self.probe_timer.stop()
            self.is_suspended = True
            self.my_console.ePopen('killall -9 bitrate', self.clear_values)

    def start(self):
        if self.is_running:
            return

        try:
            service = self.source.service
        except Exception:
            service = None

        if service:
            if DBG:
                AGDEBUG('[furyBitrate:start] initiate run_timer')
            self.run_timer.start(50, True)
        else:
            if DBG:
                AGDEBUG('[furyBitrate:start] wait 100ms for self.source.service')
            self.start_timer.start(100, True)

    def _decode_data(self, data):
        if data is None:
            return ''

        if isinstance(data, (tuple, list)) and data:
            data = data[0]

        if isinstance(data, six.text_type):
            return data

        try:
            if six.PY2:
                if isinstance(data, unicode):
                    return data
                return data.decode('utf-8', 'ignore')
            if isinstance(data, bytes):
                return data.decode('utf-8', 'ignore')
        except Exception:
            pass

        try:
            return six.text_type(data)
        except Exception:
            try:
                return str(data)
            except Exception:
                return ''

    def _build_command_variants(self, adapter, demux, vpid, apid):
        variants = []

        # Different images / binaries use different argument orders.
        if isImageType('vti'):
            variants.append((demux, vpid, apid))
            variants.append((adapter, demux, vpid, apid))
        else:
            variants.append((adapter, demux, vpid, apid))
            variants.append((demux, vpid, apid))

        # Remove duplicates while preserving order.
        deduped = []
        for item in variants:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def run_bitrate(self):
        if DBG:
            AGDEBUG('[furyBitrate:run_bitrate] >>>')

        if self.is_suspended:
            return

        if not self.bitrate_bin:
            self.last_error = 'bitrate binary not found'
            if DBG:
                AGDEBUG('[furyBitrate:run_bitrate] {}'.format(self.last_error))
            self.run_timer.start(2000, True)
            return

        adapter = 0
        demux = 0

        try:
            stream = self.source.service.stream()
            if stream:
                stream_data = stream.getStreamingData()
                if stream_data:
                    demux = max(stream_data.get('demux', 0), 0)
                    adapter = max(stream_data.get('adapter', 0), 0)
        except Exception as e:
            if DBG:
                AGDEBUG('[furyBitrate:run_bitrate] Exception collecting stream data: {}'.format(e))

        try:
            info = self.source.service.info()
            vpid = info.getInfo(iServiceInformation.sVideoPID)
            apid = info.getInfo(iServiceInformation.sAudioPID)
        except Exception as e:
            if DBG:
                AGDEBUG('[furyBitrate:run_bitrate] Exception collecting service info: {}'.format(e))
            self.run_timer.start(500, True)
            return

        if vpid < 0 and apid < 0:
            if DBG:
                AGDEBUG('[furyBitrate:run_bitrate] Skipping - no valid PIDs')
            self.run_timer.start(500, True)
            return

        vpid = max(vpid, 0)
        apid = max(apid, 0)
        self.current_apid = apid

        self.command_variants = self._build_command_variants(adapter, demux, vpid, apid)
        if self.last_working_variant is not None and self.last_working_variant < len(self.command_variants):
            self.variant_index = self.last_working_variant
        elif self.variant_index >= len(self.command_variants):
            self.variant_index = 0

        self._reset_parser_state()
        self.is_running = True

        args = self.command_variants[self.variant_index]
        arg_string = ' '.join([str(x) for x in args])
        cmd = 'killall -9 bitrate > /dev/null 2>&1; nice {} {}'.format(self.bitrate_bin, arg_string)

        if DBG:
            AGDEBUG('[furyBitrate:run_bitrate] starting variant {} -> "{}"'.format(self.variant_index, cmd))

        self.probe_timer.start(2500, True)
        retval = self.container.execute(cmd)
        if retval:
            self.probe_timer.stop()
            self.is_running = False
            if DBG:
                AGDEBUG('[furyBitrate:run_bitrate] execute returned {}'.format(retval))
            self._advance_variant_or_retry()

    def _advance_variant_or_retry(self):
        if self.last_working_variant is None and self.command_variants and self.variant_index + 1 < len(self.command_variants):
            self.variant_index += 1
            self.run_timer.start(100, True)
        else:
            if self.last_working_variant is not None:
                self.variant_index = self.last_working_variant
            else:
                self.variant_index = 0
            self.run_timer.start(500, True)

    def probe_timeout(self):
        if DBG:
            AGDEBUG('[furyBitrate:probe_timeout] parsed_this_run={}'.format(self.parsed_this_run))

        if self.parsed_this_run or self.is_suspended:
            return

        # Wrong argument layout or bad binary can leave the process running
        # without producing parsable output. Kill it and try the next variant.
        self.my_console.ePopen('killall -9 bitrate')

    def app_closed(self, retval):
        if DBG:
            AGDEBUG('[furyBitrate:app_closed] retval={}, parsed_this_run={}, is_suspended={}'.format(retval, self.parsed_this_run, self.is_suspended))

        self.probe_timer.stop()
        self.is_running = False

        # Some builds close without a trailing newline, so flush the last chunk.
        if self.remaining_data:
            self._handle_output_line(self.remaining_data.strip())
            self.remaining_data = ''

        if self.is_suspended:
            self.clear_values()
            return

        # Accept partial-but-valid output (video line only) instead of dropping it.
        if not self.parsed_this_run and self.pending_stats:
            if len(self.pending_stats) >= 2:
                self._apply_stats(self.pending_stats[0], self.pending_stats[1])
            else:
                self._apply_stats(self.pending_stats[0], None)
            self.pending_stats = []

        if not self.parsed_this_run:
            self._advance_variant_or_retry()
        else:
            self.last_working_variant = self.variant_index
            self.run_timer.start(300, True)

    def _apply_stats(self, video_stats, audio_stats=None):
        if video_stats:
            self.vmin, self.vmax, self.vavg, self.vcur = video_stats[:4]

        if audio_stats:
            self.amin, self.amax, self.aavg, self.acur = audio_stats[:4]
        elif self.current_apid <= 0:
            self.amin = self.amax = self.aavg = self.acur = 0

        self.parsed_this_run = True
        self.last_working_variant = self.variant_index
        self.probe_timer.stop()
        Converter.changed(self, (self.CHANGED_POLL,))

    def _handle_output_line(self, line):
        if not line:
            return

        lower = line.lower()
        if 'exec format error' in lower or 'not found' in lower or 'permission denied' in lower:
            self.last_error = line
            if DBG:
                AGDEBUG('[furyBitrate:output] {}'.format(line))
            return

        numbers = _int_re.findall(line)
        if len(numbers) >= 8:
            try:
                video_stats = list(map(int, numbers[:4]))
                audio_stats = list(map(int, numbers[4:8]))
                self._apply_stats(video_stats, audio_stats)
            except Exception as e:
                if DBG:
                    AGDEBUG('[furyBitrate:handle_output_line] Parse error {}'.format(e))
            return

        if len(numbers) >= 4:
            try:
                self.pending_stats.append(list(map(int, numbers[:4])))
            except Exception as e:
                if DBG:
                    AGDEBUG('[furyBitrate:handle_output_line] Parse error {}'.format(e))
                return

            if len(self.pending_stats) >= 2:
                self._apply_stats(self.pending_stats[0], self.pending_stats[1])
                self.pending_stats = []
            elif self.current_apid <= 0:
                self._apply_stats(self.pending_stats[0], None)
                self.pending_stats = []

    def data_avail(self, data):
        if DBG:
            AGDEBUG('[furyBitrate:data_avail] >>> {}'.format(repr(data)))

        data_str = self._decode_data(data)
        if not data_str:
            return

        data_str = data_str.replace('\r', '')
        combined = self.remaining_data + data_str
        lines = combined.split('\n')

        if combined.endswith('\n'):
            self.remaining_data = ''
            complete_lines = lines[:-1]
        else:
            self.remaining_data = lines[-1]
            complete_lines = lines[:-1]

        for line in complete_lines:
            self._handle_output_line(line.strip())
