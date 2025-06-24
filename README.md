# Pulse MCU

Implements a little bit of the Mackie MIDI control surface protocol to let you
adjust your software/hardware mixers with [Pulseaudio][pa] (and thus also
[Pipewire][pw]).

It's worth note that I implemented this for the **PreSonus FaderPort 16**,
which... took some liberties with the nominally 8-port Mackie standard to make
it work with 16 strips. The configuration may be odd, but I'll accept a PR to
make it work with the standard MCU protocol as well---just note that I don't
have any hardware with which to test that.

[pa]: https://www.freedesktop.org/wiki/Software/PulseAudio/
[pw]: https://www.pipewire.org/

## Preparation

If you haven't already, create a virtual environment. I like to do so in the
repo; you can do so wherever.

```
python3 -m venv venv
. venv/bin/activate
```

(You may need a package for the `venv` module, like `python3-venv`. Check your
package manager for more details.)

In the virtual environment, install your packages.

```
pip install -r requirements.txt
```

(Your `pip` here should end up being the one you used to create the
environment. Again, you might need a package for this, typically
`python3-pip`.)

## Running

The root dir is a bit of a mess due to diagnostic tools and first attempts.
With the environment active (`. venv/bin/activate`), you should be able to do
this:

```
python pulse_mcu.py
```

This *should* immediately connect to the board. If not, you can use another
utility (`patchage`, `qpwgraph`, even `qjackctl`) to help with the connections.

## Usage

While running, the four top scene selector buttons will show a subset of faders
on the strip:

- `Audio`: shows hardware inputs
- `VI`: shows hardware outputs
- `Bus`: shows software inputs
- `VCA`: shows software outputs
- `All`: shows everything

The program attempts to keep the order reliable so your strips don't jump
around. However, new strips appear and disappear in response to new streams.
While you're plugging in hardware in the hardware views, or if your software
sets up new streams especially rapidly (\*cough\* *Discord* \*cough\*), you can
exercise your faders quite a bit.

On a single strip:

- The fader adjust the volume. The range is supposed to resemble the scale on
  the board, except the board scale isn't linear, so it's mostly tuned around
  the (measured and slightly rounded) "unity" value and the nearest decibel
  ticks. The absolute lowest end (where -120 belongs) is forced to zero (-inf
  db) by default. (This is redundant with muting, but something most board
  operators seem to expect.)
- The `Mute` button toggles soft mute. It follows the mute state---it should
  glow when the stream is already muted.

The communication is bidirectional. If apps set the volume (stream owners or
tools like [pavucontrol][pavucontrol]), the faders are instructed to move.
Moving the fader updates the stream in real time, which should be observable to
apps listening to this (like pavucontrol).

## Known Issues

There is no support for shift-chords yet, otherwise the button arrangement
would be a little more sensible.

Python sometimes just segfaults. I think this is an upstream issue with the
Pulse bindings, and sporadically happens when attempting to create new "peak
detection" streams. If your board resets due to a disconnection, you might just
have to restart the script. Sorry.

Peak detection streams are a bit strenuous---they create a listener for *every*
audio source. (Doing so is likely the cause of the segfault above.) This isn't
unique to this project; [pavucontrol][pavucontrol] does it too. In fact, this
project carefully attempts to (1) set props to make it clear that the streams
are just peak detectors, and (2) ignore the peak detectors, including its own
and those of `pavucontrol`. This detection is heuristic; let me know if it goes
haywire.

There is no support yet for creating "user" fader pages.

[pavucontrol]: https://www.freedesktop.org/software/pulseaudio/pavucontrol/

## License

Gnu GPL version 3 or later. See `COPYING`.

## Support

Let me know if you find this useful, or if you find any bugs. The list above is
non-exhaustive, but knowing someone relies on it is good motivation to fix
things. PRs are also welcome!
