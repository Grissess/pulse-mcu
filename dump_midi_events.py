import time
from rtmidi2 import *

def callback_with_source(src, msg, time):
    msgtype, channel = splitchannel(msg[0])
    print(f"Message generated from {src}: {channel=}, {msgtype=}, data: {msg[1:]} raw {list(map(hex, msg))}")

midiin = MidiInMulti()
midiin.open_ports("*")
midiin.callback = callback_with_source  
while True: time.sleep(60)
