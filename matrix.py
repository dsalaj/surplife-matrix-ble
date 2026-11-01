"""Driver for Surplife BLE LED matrix panels (IOTBT*, e.g. board ZJ-PSC-HC2-RGB-1512-A, 90x12).

Protocol knowledge from:
  - https://github.com/lambdafu/surplife  (PROTOCOL.md, 96x16 sibling device)
  - https://codeberg.org/ge0rg/surplife-curtain

IMPORTANT firmware quirk: the device leaks memory per BLE connection and
locks up after ~6-7 connects since power-on (needs a power cycle). Do as much
as possible inside ONE session (one `async with Matrix()` block / one CLI run).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import sys
import uuid

from bleak import BleakClient, BleakScanner

WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "IOTBT"
MAX_SEGMENT = 490


async def find_panel(name: str | None = None, timeout: float = 15.0):
    """Find a panel by full name / suffix, or the strongest-signal IOTBT* device."""
    if name and not name.upper().startswith(NAME_PREFIX):
        name = NAME_PREFIX + name.upper()
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    panels = [(adv.rssi, dev) for dev, adv in found.values()
              if (adv.local_name or dev.name or "").startswith(NAME_PREFIX)
              and (name is None or (adv.local_name or dev.name) == name)]
    if not panels:
        raise RuntimeError(f"no panel {name or NAME_PREFIX + '*'} found "
                           "(powered? Surplife phone app still connected?)")
    return max(panels, key=lambda p: p[0])[1]


class Matrix:
    def __init__(self, name: str | None = None, len_mode: str = "be16",
                 verbose: bool = False, timeout: float = 5.0):
        self.name = name  # full BLE name, short suffix ("FC9"), or None = nearest IOTBT*
        self.cols = self.rows = None  # filled from the device status after init
        self.len_mode = len_mode  # "be16" (transport v1) or "u8" (v0)
        self.verbose = verbose
        self.timeout = timeout
        self.seq = 0
        self.client: BleakClient | None = None
        self.rx: asyncio.Queue[bytes] = asyncio.Queue()
        self.status: dict = {}

    # ── connection ────────────────────────────────────────────────
    async def __aenter__(self):
        dev = await find_panel(self.name)
        self.name = dev.name or self.name
        self.client = BleakClient(dev, timeout=15)
        await self.client.connect()
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        await self.init()
        return self

    async def __aexit__(self, *exc):
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    def _log(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr)

    def _on_notify(self, _h, data: bytearray):
        self._log("<<", bytes(data).hex())
        self.rx.put_nowait(bytes(data))

    # ── framing ───────────────────────────────────────────────────
    def wrap(self, inner: bytes, len_mode: str | None = None) -> bytes:
        """[flags][seq][80 00][len-1 BE16][len]: flags bit0 = transport v1,
        which uses a BE16 second length field; v0 uses a single byte."""
        mode = len_mode or self.len_mode
        self.seq = (self.seq + 1) & 0xFF
        n = len(inner)
        flags = 0x01 if mode == "be16" else 0x00
        hdr = bytes([flags, self.seq]) + b"\x80\x00" + (n - 1).to_bytes(2, "big")
        if mode == "be16":
            hdr += n.to_bytes(2, "big")
        else:
            if n > 0xFF:
                raise ValueError("v0 framing cannot encode >255 bytes")
            hdr += bytes([n])
        return hdr + inner

    @staticmethod
    def unwrap(raw: bytes) -> bytes:
        """Inner payload (starting with 0x15/0x16) of a device reply."""
        return raw[8:] if raw[0] & 0x01 else raw[7:]

    async def write_raw(self, inner: bytes, len_mode: str | None = None):
        pkt = self.wrap(inner, len_mode)
        self._log(">>", pkt.hex())
        await self.client.write_gatt_char(WRITE_UUID, pkt, response=False)

    async def send(self, cmd: bytes, len_mode: str | None = None):
        await self.write_raw(b"\x0a" + cmd, len_mode)

    async def wait_for(self, *prefix: int, timeout: float | None = None, skip: int = 0) -> bytes:
        """Wait for reply whose inner payload (after `skip` bytes) starts with prefix."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout or self.timeout)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"no reply {bytes(prefix).hex()}")
            raw = await asyncio.wait_for(self.rx.get(), remaining)
            inner = self.unwrap(raw)
            if inner[skip: skip + len(prefix)] == bytes(prefix):
                return inner

    async def send_wait(self, cmd: bytes, *prefix: int, **kw) -> bytes:
        await self.send(cmd, kw.pop("len_mode", None))
        return await self.wait_for(*prefix, **kw)

    def _parse_status(self, inner: bytes) -> dict:
        if len(inner) >= 19:
            self.cols, self.rows = inner[17], inner[18]
            self.status = {"power": inner[7] != 0x24, "speed": inner[10],
                           "brightness": inner[14], "cols": inner[17], "rows": inner[18]}
        return self.status

    # ── handshake ─────────────────────────────────────────────────
    async def init(self):
        now = datetime.datetime.now()
        ts = bytes([0x10, 0x14, now.year - 2000, now.month, now.day, now.hour,
                    now.minute, now.second, now.isoweekday(), 0x00, 0x0F])
        ts += bytes([sum(ts) & 0xFF])
        await self.write_raw(b"\x0c")
        await self.send(ts)
        await self.send(b"\xea\x81\x8a\x8b\x59")
        st = await self.wait_for(0xEA, 0x81, skip=1, timeout=6)
        try:  # device sends a second ea81 when ready; commands before it are ignored
            st = await self.wait_for(0xEA, 0x81, skip=1, timeout=3)
        except TimeoutError:
            pass
        self._parse_status(st)
        self._log("status", self.status)

    # ── controls ──────────────────────────────────────────────────
    async def power(self, on: bool):
        st = await self.send_wait(bytes([0xE0, 0x01, 0x00, 0x23 if on else 0x24]) + bytes(10),
                                  0x16, 0xEA, 0x81)
        return self._parse_status(st)

    async def brightness(self, v: int, **kw):
        v = max(0, min(100, v))
        st = await self.send_wait(bytes([0xE0, 0x01, 0x00, 0x01, 0, 0, v, 0, v, 0, 0, 0, 0, 0]),
                                  0x16, 0xEA, 0x81, **kw)
        return self._parse_status(st)

    async def speed(self, v: int):
        await self.send(bytes([0xEA, 0x07, 0x00, max(1, min(100, v))]))

    async def clock(self, style: int = 0, h24: bool = True, date: bool = False):
        await self.send(bytes([0xEA, 0x10, style & 7, 2 if h24 else 1, 1 if date else 0]))

    # ── content upload ────────────────────────────────────────────
    async def upload(self, cache_type: int, meta: bytes, payload: bytes,
                     activate: bytes, c_hash: bytes, force: bool = False):
        resp = await self.send_wait(bytes([0xEA, 0x05, cache_type]) + c_hash, 0x15, 0xEA, 0x05)
        if len(resp) > 3 and resp[3] == 0x01 and not force:
            self._log("cache hit")
            await self.send(activate)
            return "cached"
        blob = b"\xea\x23\x01\x03" + c_hash + len(meta).to_bytes(2, "big") + meta + payload
        await self.send_wait(b"\xe0\x30" + len(blob).to_bytes(4, "big") + b"\xea\x23", 0x15, 0xE0, 0x30)
        nseg = (len(blob) + MAX_SEGMENT - 1) // MAX_SEGMENT
        for i in range(nseg):
            chunk = blob[i * MAX_SEGMENT:(i + 1) * MAX_SEGMENT]
            await self.send(b"\xe0\x32" + len(chunk).to_bytes(2, "big") + bytes(4) + bytes([i]) + chunk)
            if nseg > 8:
                await asyncio.sleep(0.025)
        await self.send_wait(b"\xe0\x33", 0x15, 0xE0, 0x33, timeout=10)
        await asyncio.sleep(0.5)
        await self.send(b"\xe0\x1e\x00")
        await self.send(b"\xe0\x1e\x00")
        try:
            await self.wait_for(0x15, 0xEA, 0x24, timeout=3)
        except TimeoutError:
            pass
        await self.send(activate)
        return "uploaded"

    async def show_gif(self, gif: bytes, speed: int = 50, force: bool = False):
        meta = json.dumps({"v": 1, "mant_type": 0, "enable_a2pl": 1,
                           "layers": [{"nm": str(uuid.uuid4()), "type": "b", "amt_pos": 0,
                                       "frame_num": 0, "amt_length": len(gif), "amt_fmt": 2}],
                           "all_file_type": "b"}, separators=(",", ":")).encode()
        h = hashlib.md5(b"b" + gif).digest()
        r = await self.upload(0x01, meta, gif, bytes([0xEA, 0x07, 0x00, speed]), h, force)
        await asyncio.sleep(3)  # let GIF playback settle before more traffic
        return h, r


async def _scan():
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    for dev, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
        n = adv.local_name or dev.name or ""
        if n.startswith(NAME_PREFIX):
            print(f"{n}  rssi={adv.rssi}  {dev.address}")


# ── CLI: one session, many actions ────────────────────────────────
async def _main(argv):
    p = argparse.ArgumentParser(description="Surplife 90x12 LED matrix control (one BLE session)")
    p.add_argument("-n", "--name", help="panel BLE name or suffix, e.g. IOTBTFC9 or FC9 (default: nearest)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--len-mode", default="be16", choices=["be16", "u8"])
    p.add_argument("actions", nargs="+",
                   help="scan (alone) | status | on | off | bright:N | speed:N | clock:STYLE | "
                        "gif:PATH[:speed] | raw:HEX | sleep:S")
    a = p.parse_args(argv)
    if a.actions == ["scan"]:  # no connection, safe w.r.t. the connect limit
        return await _scan()
    async with Matrix(a.name, a.len_mode, a.verbose) as m:
        print(f"{m.name}: {m.cols}x{m.rows}", m.status)
        for act in a.actions:
            cmd, _, arg = act.partition(":")
            if cmd == "status":
                print(m.status)
            elif cmd in ("on", "off"):
                print(await m.power(cmd == "on"))
            elif cmd == "bright":
                print(await m.brightness(int(arg)))
            elif cmd == "speed":
                await m.speed(int(arg))
            elif cmd == "clock":
                await m.clock(int(arg or 0))
            elif cmd == "gif":
                path, _, spd = arg.partition(":")
                data = open(path, "rb").read()
                gw, gh = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
                if (gw, gh) != (m.cols, m.rows):
                    print(f"WARNING: {path} is {gw}x{gh}, panel is {m.cols}x{m.rows}; "
                          f"convert with: make_gif.py SRC DST --size {m.cols}x{m.rows}")
                h, r = await m.show_gif(data, int(spd or 50))
                print(f"gif {path}: {r} hash={h.hex()}")
            elif cmd == "raw":
                await m.send(bytes.fromhex(arg))
            elif cmd == "sleep":
                await asyncio.sleep(float(arg))
            else:
                raise SystemExit(f"unknown action {act}")
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
