# surplife-matrix-ble

Control Surplife Bluetooth LED matrix panels (ZJ-PSC-HC2-RGB-1512-A) from Python — no app needed.
Not affiliated with Surplife.

Works with panels advertising as `IOTBT*` over BLE. Tested on a **90 x 12** panel
(board marking ZJ-PSC-HC2-RGB-1512-A); the panel size is read from the device.

## Setup
```sh
python3 -m venv .venv && .venv/bin/pip install bleak pillow
```
Close the Surplife phone app first (the panel accepts one BLE connection).

## Usage
```sh
# find panels (no connection) and check size
.venv/bin/python matrix.py scan
.venv/bin/python matrix.py status            # prints e.g. IOTBTFC9: 90x12

# convert any GIF/APNG/WebP/PNG to a device-safe GIF of your panel size
.venv/bin/python make_gif.py my_anim.gif animations/my_anim.gif --size 90x12 --mode fill   # or fit / stretch

# one BLE session, several actions in order
.venv/bin/python matrix.py gif:animations/my_anim.gif:60      # upload + play, speed 60
.venv/bin/python matrix.py bright:40 on off clock:3 sleep:2 -v
```
Without `-n`, the nearest `IOTBT*` panel is used; `-n FC9` / `-n IOTBTFC9` picks one.
Actions: `status on off bright:N speed:N clock:STYLE gif:PATH[:speed] raw:HEX sleep:S`.
Use from Python: `async with Matrix() as m: await m.show_gif(data)`.

## Firmware gotchas (important)
- **~6-7 BLE connects per power-on, then it locks up** (panel freezes, needs unplugging).
  Batch actions into one `matrix.py` run; for frequent changes keep one long-lived connection.
- GIFs must have a **single global palette**, **>=100 ms frame delay**, **<=~60 frames**,
  **<=~52 KB**; otherwise the firmware can crash. `make_gif.py` enforces this.
- Re-uploading identical content hits the device cache (instant).

## Protocol notes
Packet: `[flags][seq][80 00][len-1 BE16][len BE16 if flags&1 else u8][0x0a][cmd]`.
Init: raw `0c`, time sync `10 14 ...`, `ea 81 8a 8b 59`, wait for two `ea 81` status replies.
Upload: `ea 05` cache check -> `e0 30` size -> `e0 32` 490-byte segments -> `e0 33` -> `e0 1e 00` x2 -> `ea 07 00 <speed>`.

Based on [lambdafu/surplife](https://github.com/lambdafu/surplife) (96x16 sibling device,
see its PROTOCOL.md for playlist/text/image commands) and
[ge0rg/surplife-curtain](https://codeberg.org/ge0rg/surplife-curtain).

## License
MIT, see [LICENSE](LICENSE).
