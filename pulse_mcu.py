# Huge amounts of this are cribbed from TouchMCU's documentation at
# https://github.com/NicoG60/TouchMCU/blob/main/doc/mackie_control_protocol.md
# Seriously, thanks NicoG60!

import sys
import time
import asyncio
import random
import traceback
import itertools
import math
from enum import Enum, IntEnum
from pprint import pprint
if sys.version_info >= (3, 11):
    from asyncio import TaskGroup
else:
    from taskgroup import TaskGroup

import pulsectl_asyncio
from pulsectl_asyncio.pulsectl_async import PulseEventTypeEnum, PulseEventFacilityEnum, PulseIndexError
import rtmidi2

class DecibelRange:
    def __init__(self, lower=-80, upper=25, true_zero=True):
        self.lower, self.upper = lower, upper
        self.true_zero = true_zero

    @property
    def range(self):
        return self.upper - self.lower

    def fullscale_from_lin(self, lin):
        lin = abs(lin)
        if lin == 0:
            if self.true_zero:
                return -math.inf
            return self.lower
        return max(self.lower, min(self.upper, 60 * math.log(lin, 10)))

    def fullscale_to_lin(self, db):
        if math.isinf(db) and db < 0:
            return 0
        return 10 ** (db / 60)

    def unit_from_lin(self, lin):
        if lin == 0:
            return -math.inf
        return (self.fullscale_from_lin(lin) - self.lower) / self.range

    def unit_to_lin(self, unit):
        if self.true_zero and unit == 0:
            return 0
        return self.fullscale_to_lin(self.lower + self.range * unit)

DecibelRange.DEFAULT = DecibelRange()
DecibelRange.METER = DecibelRange(-120, 0)

class StreamKind(Enum):
    HARD_IN = 1
    HARD_OUT = 2
    APP_IN = 3
    APP_OUT = 4

class PulseMonitor:
    def __init__(self, source, index = None):
        self.source = source
        self.index = index

    def __repr__(self):
        return f'<PulseMonitor {self.source!r} {self.index!r}>'

    def subscribe_sample_peak(self, pulse, rate=5):
        print(f'open {self.source.name} {self.index}')
        return pulse.subscribe_peak_sample(self.source.name, stream_idx=self.index, rate=rate)

class PulseStream:
    def __init__(self, model, info, kind, monitor = None):
        self.model = model
        self.info, self.kind = info, kind
        self.monitor = monitor if monitor is not None else PulseMonitor(info, None)
        self.closed = False

    def __repr__(self):
        monitor = 'is the same' if self.monitor.source is self.info and self.monitor.index is None else repr(self.monitor)
        return f'<PulseStream for {self.info!r}, {self.kind.name}, monitor {monitor}>'

    def subscribe_sample_peak(self, rate=5):
        return self.monitor.subscribe_sample_peak(self.model.pulse, rate)

    INFO_FUNCS = {
            StreamKind.HARD_IN: 'source_info',
            StreamKind.HARD_OUT: 'sink_info',
            StreamKind.APP_IN: 'source_output_info',
            StreamKind.APP_OUT: 'sink_input_info',
    }
    async def update(self):
        try:
            self.info = await getattr(self.model.pulse, self.INFO_FUNCS[self.kind])(self.info.index)
        except PulseIndexError:
            self.closed = True

    async def get_volume(self):
        return await self.model.pulse.volume_get_all_chans(self.info)

    async def set_volume(self, vol):
        await self.model.pulse.volume_set_all_chans(self.info, vol)
        await self.update()

    def get_is_muted(self):
        return self.info.mute

    def get_name(self):
        return self.info.name

    def get_app_name(self):
        return self.info.proplist.get('application.name', '')

    async def set_is_muted(self, value):
        await self.model.pulse.mute(self.info, value)
        await self.update()

class PulseModel:
    def __init__(self, pulse, view):
        self.pulse = pulse
        self.view = view
        self.streams = {}

    async def initialize(self, tg):
        self.tg = tg
        self.event_task = self.tg.create_task(self.events())
        await self.update()

    # Pulse uses Peak Detect, and pulsectl_asyncio uses peak detect, as the name
    # of a stream that listens with peak detection at a downsync'd rate.
    # If we get events about them, ignore them, since they churn a lot.
    def uninteresting_stream(self, info):
        return info.name.lower() == 'peak detect'

    async def update(self):
        self.streams.clear()
        for src in await self.pulse.source_list():
            if self.uninteresting_stream(src): continue
            self.streams[src.index] = PulseStream(self, src, StreamKind.HARD_IN)
        for snk in await self.pulse.sink_list():
            if self.uninteresting_stream(snk): continue
            mon = await self.pulse.source_info(snk.monitor_source)
            self.streams[snk.index] = PulseStream(self, snk, StreamKind.HARD_OUT,
                PulseMonitor(mon))

        for src in await self.pulse.source_output_list():
            if self.uninteresting_stream(src): continue
            self.streams[src.index] = PulseStream(self, src, StreamKind.APP_IN)
        for snk in await self.pulse.sink_input_list():
            if self.uninteresting_stream(snk): continue
            sink = await self.pulse.sink_info(snk.sink)
            mon = await self.pulse.source_info(sink.monitor_source)
            self.streams[snk.index] = PulseStream(self, snk, StreamKind.APP_OUT,
                PulseMonitor(mon, snk.index))
    INFO_SOURCE = {
            PulseEventFacilityEnum.sink: 'sink_info',
            PulseEventFacilityEnum.sink_input: 'sink_input_info',
            PulseEventFacilityEnum.source: 'source_info',
            PulseEventFacilityEnum.source_output: 'source_output_info',
    }
    async def events(self):
        async for ev in self.pulse.subscribe_events('all'):
            print('pulse event', ev)
            full = ev.t != PulseEventTypeEnum.change
            if full:
                await self.update()
                await self.view.refresh()
            else:
                if ev.index not in self.streams:
                    # We're not monitoring this because it's probably a peaker. Ignore.
                    continue
                await self.view.stream_update(ev.index)

class PulseStrip:
    def __init__(self, model, stream):
        self.stream = stream
        print(stream)

    @property
    def index(self):
        return self.stream.info.index

    async def send_to(self, panel, strip):
        print('send_to start', panel, strip)
        panel.set_pos(strip, DecibelRange.DEFAULT.unit_from_lin(await self.stream.get_volume()))
        panel.set_mute(strip, self.stream.get_is_muted())
        panel.set_text(strip, 0, self.stream.get_app_name())
        panel.set_text(strip, 1, self.stream.get_name())
        print('send_to done', panel, strip)

class PulseView:
    class View(Enum):
        HARD_IN = 1
        HARD_OUT = 2
        APP_IN = 3
        APP_OUT = 4
        ALL = 5

    PEAK_RATE = 25

    def __init__(self, model, panel, tg, width):
        self.model = model
        self.panel = panel
        self.tg = tg
        self.strips = [None] * width
        self.view = self.View.ALL
        self.init_task = self.tg.create_task(self.set_view(self.View.ALL))
        self.peakers = {}

    async def stream_update(self, index):
        for sidx, strip in enumerate(self.strips):
            if strip and strip.index == index:
                await strip.stream.update()
                await self.send_strip(sidx, strip)

    async def set_view(self, view):
        self.view = view
        if view != self.View.ALL:
            streams = [stream for stream in self.model.streams.values() if stream.kind._value_ == view._value_]
        else:
            streams = list(self.model.streams.values())
        for sidx, stream in itertools.zip_longest(range(len(self.strips)), streams):
            if sidx is None:
                break
            await self.set_strip(sidx, PulseStrip(self.model, stream) if stream is not None else None)

    async def refresh(self):
        await self.set_view(self.view)

    async def send_strip(self, sidx, strip=None):
        if not self.panel:
            return
        if strip is None:
            self.panel.set_pos(sidx, 0.0)
            self.panel.set_solo(sidx, False)
            self.panel.set_mute(sidx, False)
            self.panel.set_select(sidx, False)
            self.panel.set_text(sidx, [0,1], '')
            self.panel.set_meter(sidx, FP16.MeterKind.NONE, 0)
            return
        await strip.send_to(self.panel, sidx)

    async def set_strip(self, sidx, strip):
        print('set_strip', sidx, strip)
        self.strips[sidx] = strip
        pkinfo = self.peakers.get(sidx)
        if strip is None or (pkinfo is None and strip is not None) or (pkinfo is not None and pkinfo[1] != strip.stream.info.index):
            if pkinfo is not None:
                pkinfo[0].cancel()
                del self.peakers[sidx]
            if strip is not None:
                self.peakers[sidx] = (self.tg.create_task(self.peaker(sidx, strip)), strip.stream.info.index)
        await self.send_strip(sidx, strip)

    async def peaker(self, sidx, strip):
        async for sample in strip.stream.subscribe_sample_peak(self.PEAK_RATE):
            db = DecibelRange.METER.fullscale_from_lin(sample)
            unit = DecibelRange.METER.unit_from_lin(sample)
            #print('peaker for', sidx, 'ix', strip.stream.info.index, 'sample', sample, 'db', db, 'unit', unit)
            self.panel.set_meter(sidx, FP16.MeterKind.BAR, unit)

    async def change_volume(self, sidx, value):
        strip = self.strips[sidx]
        if strip is None:
            return
        await strip.stream.set_volume(value)

    async def toggle_mute(self, sidx):
        strip = self.strips[sidx]
        if strip is None:
            return
        await strip.stream.set_is_muted(not strip.stream.get_is_muted())

def lrange(base, width):
    return range(base, base + width)

# rtmidi2 has its own constants of this ilk, but they're not replete; this fills the gaps.
class MIDI(IntEnum):
    NOTEOFF = 0x80
    NOTEON = 0x90
    AFTERTOUCH = 0xA0
    CONTROL = 0xB0
    PROGRAM = 0xC0
    CHAN_AFTERTOUCH = 0xD0
    PITCHBEND = 0xE0
    SYSEX = 0xF0

class FP16:
    STRIPS = 16

    PBEND_MAX = 16383
    CC_MAX = 127

    # Strips 0-7
    CC_B1_METER_BASE = 0x30
    CC_B1_METER_TYPE_BASE = 0x38
    # Strips 8-15
    CC_B2_METER_BASE = 0x40
    CC_B2_METER_TYPE_BASE = 0x48

    class MeterKind(IntEnum):
        SLIT = 0
        PAN = 1
        BAR = 2
        WIDTH = 3
        NONE = 4

    class Align(IntEnum):
        CENTER = 0
        LEFT = 1
        RIGHT = 2

    LINE_WIDTH = [8, 9, 5]  # in apparent characters

    SYSHDR = [0, 1, 6, 0x16]
    SYS_TXT = 0x12  # followed by (strip), (line), (alignment), (string...)

    UNITY_RATIO = 0.75  # about where unity is on my board

    class Button(IntEnum):
        ARM = 0x00  # Shift: Arm All
        SOLO_CLEAR = 0x01
        MUTE_CLEAR = 0x02
        BYPASS = 0x03  # Shift: [Bypass] All
        MACRO = 0x04  # Shift: Open
        LINK = 0x05  # Shift: Lock
        SHIFT_R = 0x06
        SELECT_S8 = 0x07  # An exception to SELECT_BASE below

        SOLO_B1_BASE = 0x08
        MUTE_B1_BASE = 0x10
        SELECT_BASE = 0x18  # BUT!!! Strip 8 (the 9th), nominally 0x20, is 0x07 instead, see above
        FX_KNOB = 0x20  # in MCU, there'd be 8 of these, but the FP16 only has one (and it's the reason for the above)

        TRACK = 0x28  # Shift: Timecode
        SENDS = 0x29
        PAN = 0x2A
        PLUGIN = 0x2B

        PREV = 0x2E
        NEXT = 0x2F

        CHANNEL = 0x36  # Shift: F1
        ZOOM = 0x37  # Shift: F2
        SCROLL = 0x38  # Shift: F3
        BANK = 0x39  # Shift: F4
        MASTER = 0x3A  # Shift: F5
        CLICK = 0x3B  # Shift: F6
        SECTION = 0x3C  # Shift: F7
        MARKER = 0x3D  # Shift: F8

        AUDIO = 0x3E  # Shift: Inputs
        VI = 0x3F  # Shift: MIDI
        BUS = 0x40  # Shift: Outputs
        VCA = 0x41  # Shift: FX
        ALL = 0x42  # Shift: User

        SHIFT_L = 0x46

        # Not exactly in order...
        READ = 0x4A  # Shift: User 3
        WRITE = 0x4B  # Shift: User 2
        TRIM = 0x4C  # Shift: Redo
        TOUCH = 0x4D  # Shift: User 1
        LATCH = 0x4E  # Shift: Save
        OFF = 0x4F  # Shift: Undo

        SOLO_B2_BASE = 0x50  # With exceptions as below:
        JOG_KNOB = 0x53  # Strip 11 moved to 0x58
        LOOP = 0x56  # Strip 14 moved to 0x59
        SOLO_S11 = 0x58  # Moved for JOG_KNOB at 0x53
        SOLO_S14 = 0x59  # Moved for LOOP at 0x56

        BACK = 0x5B  # Indicates that BACK + FORWARD together is "RTZ", Return To Zero?
        FORWARD = 0x5C
        STOP = 0x5D
        PLAY =  0x5E  # Also pause
        RECORD = 0x5F
        FADER_TOUCH_BASE = 0x68
        MUTE_B2_BASE = 0x78

    # Views into the strip sequences, corrected for the exceptions
    BUTTONS_SOLO = list(lrange(Button.SOLO_B1_BASE, 8)) + list(lrange(Button.SOLO_B2_BASE, 8))
    BUTTONS_SOLO[11] = Button.SOLO_S11
    BUTTONS_SOLO[14] = Button.SOLO_S14
    BUTTONS_MUTE = list(lrange(Button.MUTE_B1_BASE, 8)) + list(lrange(Button.MUTE_B2_BASE, 8))
    BUTTONS_SELECT = list(lrange(Button.SELECT_BASE, 16))
    BUTTONS_SELECT[8] = Button.SELECT_S8

    MIDI_PORT_PREFIX = 'PreSonus FP16:PreSonus FP16 Port 1'

    def __init__(self, view, tg, midi_in, midi_out):
        self.view = view
        self.midi_in, self.midi_out = midi_in, midi_out
        self.last_meter_type = [0] * self.STRIPS
        # Kick off our long-running tasks...
        self.tg = tg
        self.task_heartbeat = tg.create_task(self.heartbeat())
        self.task_worker = tg.create_task(self.worker())
        self.loop = asyncio.get_event_loop()
        self.work_queue = asyncio.Queue()
        def cb(msg, time, self=self):
            try:
                self.midi_callback(msg, time)
            except Exception:
                traceback.print_exc()
        self.midi_in.callback = cb

    def set_text(self, strip, lines, s, align=Align.CENTER, highlight=False):
        # if lines is a sequence, we'll wrap across those lines in order
        if isinstance(lines, int):
            lines = [lines]
        if isinstance(s, str):
            s = s.encode()
        for line in lines:
            width = self.LINE_WIDTH[line]
            mode = align | (0x4 if highlight else 0)
            self.midi_out.send_sysex(*self.SYSHDR, self.SYS_TXT, strip, line, mode, *s[:width])
            s = s[width:]

    def set_pos(self, strip, ratio):
        print('set_pos', strip, ratio)
        ratio = min(1.0, max(0.0, ratio))
        self.midi_out.send_pitchbend(strip, int(ratio * self.PBEND_MAX))

    def set_solo(self, strip, selected):
        self.midi_out.send_noteon(0, self.BUTTONS_SOLO[strip], 127 if selected else 0)
    def set_mute(self, strip, selected):
        self.midi_out.send_noteon(0, self.BUTTONS_MUTE[strip], 127 if selected else 0)
    def set_select(self, strip, selected):
        self.midi_out.send_noteon(0, self.BUTTONS_SELECT[strip], 127 if selected else 0)

    def set_meter(self, strip, kind, value):
        base = self.CC_B1_METER_BASE if strip < 8 else self.CC_B2_METER_BASE
        type_base = self.CC_B1_METER_TYPE_BASE if strip < 8 else self.CC_B2_METER_TYPE_BASE
        send_type_change = self.last_meter_type[strip] != kind.value
        self.last_meter_type[strip] = kind.value
        if strip >= 8:
            strip -= 8
        if send_type_change:
            self.midi_out.send_cc(0, type_base + strip, kind.value)
        if value < 0: value = 0
        if value > 1: value = 1
        self.midi_out.send_cc(0, base + strip, int(value * self.CC_MAX))

    async def heartbeat(self):
        while True:
            self.midi_out.send_raw(MIDI.AFTERTOUCH, 0, 0)
            await asyncio.sleep(1)

    async def worker(self):
        while True:
            awaitable, args, kwargs = await self.work_queue.get()
            print('worker')
            await awaitable(*args, **kwargs)

    def midi_callback(self, msg, time):
        self.loop.call_soon_threadsafe(self.handle_midi, msg, time)

    def handle_midi(self, msg, time):
        tp, chan = rtmidi2.splitchannel(msg[0])
        if tp == MIDI.PITCHBEND:
            return self.handle_pos(chan, ((msg[2] << 7) | msg[1]) / self.PBEND_MAX)
        elif tp == MIDI.NOTEON:
            pitch, vel = msg[1:]
            if 0 <= pitch - self.Button.FADER_TOUCH_BASE < self.STRIPS:
                strip = pitch - self.Button.FADER_TOUCH_BASE
                return self.handle_touch(strip, vel > 0)
            try:
                strip = self.BUTTONS_SOLO.index(pitch)
                return self.handle_solo(strip, vel > 0)
            except ValueError:
                pass
            try:
                strip = self.BUTTONS_MUTE.index(pitch)
                return self.handle_mute(strip, vel > 0)
            except ValueError:
                pass
            try:
                strip = self.BUTTONS_SELECT.index(pitch)
                return self.handle_select(strip, vel > 0)
            except ValueError:
                pass

            # most regular buttons end up here
            for nm, val in self.Button.__members__.items():
                if pitch == val:
                    return self.handle_button(val, vel > 0)
        print(f'unhandled {MIDI(tp)._name_}({chan}) {list(map(hex, msg[1:]))}')

    def handle_pos(self, strip, value):
        print('handle_pos', strip, value)
        self.work_queue.put_nowait((
            self.view.change_volume,
            (strip, DecibelRange.DEFAULT.unit_to_lin(value)),
            {},
        ))

    def handle_touch(self, strip, touched):
        print('handle_touch', strip, touched)

    def handle_solo(self, strip, selected):
        print('handle_solo', strip, selected)
        strip = self.view.strips[strip]
        if selected and strip:
            pprint(strip.stream.info.proplist)

    def handle_mute(self, strip, selected):
        print('handle_mute', strip, selected)
        if selected:
            self.work_queue.put_nowait((
                self.view.toggle_mute,
                (strip,),
                {},
            ))

    def handle_select(self, strip, selected):
        print('handle_select', strip, selected)

    VIEW_BUTTONS = {
            Button.AUDIO: PulseView.View.HARD_IN,
            Button.VI: PulseView.View.HARD_OUT,
            Button.BUS: PulseView.View.APP_IN,
            Button.VCA: PulseView.View.APP_OUT,
            Button.ALL: PulseView.View.ALL,
    }
    def handle_button(self, button, selected):
        print('handle_button', button, selected)
        view = self.VIEW_BUTTONS.get(button)
        if selected and view is not None:
            self.work_queue.put_nowait((
                self.view.set_view,
                (view,),
                {},
            ))
            return

async def main():
    async with pulsectl_asyncio.PulseAsync('pulse-mcu') as pulse:
        async with TaskGroup() as tg:
            model = PulseModel(pulse, None)
            view = PulseView(model, None, tg, FP16.STRIPS)
            model.view = view
            print('model start')
            await model.initialize(tg)


            mi, mo = rtmidi2.MidiIn(), rtmidi2.MidiOut()
            pts = mi.ports_matching(FP16.MIDI_PORT_PREFIX + '*')
            print('ports:', pts)
            mi.open_port(pts[0])
            mi.callback = print
            mo.open_port(pts[0])
            fp = FP16(view, tg, mi, mo)

            #eat, my, shorts = map(bytearray, (b'eat', b'my', b'shorts'))
            #for v in (eat, my, shorts):
            #    random.shuffle(v)
            #fp.set_text(0, 0, eat)
            #fp.set_text(0, 1, my)
            #fp.set_text(0, 2, shorts)
            print('fp16 init')

            view.panel = fp
            print('running')


if __name__ == '__main__':
    asyncio.run(main())
