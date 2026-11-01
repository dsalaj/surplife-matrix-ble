"""Helpers to build device-safe 90x12 GIFs (single global palette, >=100 ms/frame)."""
import colorsys, io, math, sys
from PIL import Image

W, H = 90, 12


def save_safe_gif(frames, path, delay_ms=150, colors=128):
    """Quantize all frames to ONE shared palette (device crashes on local color tables)."""
    frames = [f.convert("RGB").resize((W, H), Image.NEAREST) if f.size != (W, H) else f.convert("RGB")
              for f in frames]
    # build a shared palette from a strip containing all frames
    strip = Image.new("RGB", (W, H * len(frames)))
    for i, f in enumerate(frames):
        strip.paste(f, (0, i * H))
    pal_img = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal_img, dither=Image.Dither.NONE) for f in frames]
    delay_ms = max(100, delay_ms)
    q[0].save(path, save_all=True, append_images=q[1:], duration=delay_ms, loop=0,
              optimize=False, disposal=1)
    data = strip_local_color_tables(open(path, "rb").read())
    open(path, "wb").write(data)
    rep = gif_report(data)
    print(f"{path}: {rep}")
    if not rep["ok"]:
        raise ValueError("GIF outside device-safe envelope")
    return data


def rainbow_test(n=24):
    frames = []
    for t in range(n):
        im = Image.new("RGB", (W, H))
        px = im.load()
        for x in range(W):
            r, g, b = colorsys.hsv_to_rgb(((x + t * 90 / n) / W) % 1.0, 1, 1)
            for y in range(H):
                px[x, y] = (int(r * 255), int(g * 255), int(b * 255))
        # white marker in the top-left pixel + border so we can verify orientation
        px[0, 0] = (255, 255, 255)
        frames.append(im)
    return frames




def gif_report(data: bytes) -> dict:
    """Count frames / local color tables / min delay by walking GIF blocks."""
    assert data[:6] in (b"GIF87a", b"GIF89a")
    packed = data[10]
    i = 13 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0)
    frames = lct = 0
    delays = []
    while i < len(data):
        b = data[i]
        if b == 0x21:
            if data[i + 1] == 0xF9:
                delays.append(data[i + 4] | data[i + 5] << 8)
            i += 2
            while data[i]:
                i += data[i] + 1
            i += 1
        elif b == 0x2C:
            frames += 1
            ip = data[i + 9]
            i += 10
            if ip & 0x80:
                lct += 1
                i += 3 * (2 << (ip & 7))
            i += 1
            while data[i]:
                i += data[i] + 1
            i += 1
        elif b == 0x3B:
            break
        else:
            i += 1
    return {"global_ct": bool(packed & 0x80), "frames": frames, "local_cts": lct,
            "min_delay_ms": 10 * min(delays) if delays else None, "bytes": len(data),
            "ok": bool(packed & 0x80) and lct == 0 and (not delays or min(delays) >= 10)
                  and len(data) <= 52 * 1024}


def strip_local_color_tables(data: bytes) -> bytes:
    """Remove per-frame color tables that duplicate the global one."""
    packed = data[10]
    gsize = 3 * (2 << (packed & 7))
    gct = data[13:13 + gsize]
    out = bytearray(data[:13 + gsize])
    i = 13 + gsize
    while i < len(data):
        b = data[i]
        if b == 0x2C:
            desc = bytearray(data[i:i + 10])
            ip = desc[9]
            i += 10
            if ip & 0x80:
                lsize = 3 * (2 << (ip & 7))
                lct = data[i:i + lsize]
                if lct[:gsize] != gct[:lsize] and lct != gct:
                    raise ValueError("frame palette differs from global; cannot strip")
                i += lsize
                desc[9] = ip & 0x78  # clear LCT flag + size, keep interlace/sort
            out += desc
            start = i
            i += 1
            while data[i]:
                i += data[i] + 1
            i += 1
            out += data[start:i]
        elif b == 0x21:
            start = i
            i += 2
            while data[i]:
                i += data[i] + 1
            i += 1
            out += data[start:i]
        else:
            out += data[i:]
            break
    return bytes(out)


def load_frames(path, mode="fill"):
    """Load any Pillow-readable image/animation, return (frames 90x12 RGB, delay_ms)."""
    from PIL import ImageOps, ImageSequence
    src = Image.open(path)
    delay = src.info.get("duration", 150) or 150
    frames = []
    for f in ImageSequence.Iterator(src):
        f = f.convert("RGB")
        if mode == "fill":
            f = ImageOps.fit(f, (W, H), Image.LANCZOS)
        elif mode == "fit":
            f = ImageOps.pad(f, (W, H), Image.LANCZOS, color=(0, 0, 0))
        else:  # stretch
            f = f.resize((W, H), Image.LANCZOS)
        frames.append(f)
    return frames, delay


def convert(src, dst, mode="fill", delay_ms=None, max_frames=60):
    frames, delay = load_frames(src, mode)
    delay = delay_ms or delay
    if len(frames) > max_frames:  # drop frames evenly, keep total duration
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
        delay = int(delay * step)
    for colors in (128, 64, 32):
        try:
            return save_safe_gif(frames, dst, delay_ms=delay, colors=colors)
        except ValueError as e:
            print(f"  {colors} colors too big ({e}), retrying with fewer")
    raise SystemExit("could not fit GIF in safe envelope; shorten the animation")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Convert image/animation to a device-safe 90x12 GIF")
    p.add_argument("src", nargs="?", help="input file (GIF/APNG/WebP/PNG/JPG); omit for test pattern")
    p.add_argument("dst", nargs="?", default="animations/test_rainbow.gif")
    p.add_argument("--mode", choices=["fill", "fit", "stretch"], default="fill",
                   help="fill=crop to 90x12, fit=letterbox, stretch=distort")
    p.add_argument("--delay", type=int, help="ms per frame (min 100)")
    a = p.parse_args()
    if a.src:
        convert(a.src, a.dst, a.mode, a.delay)
    else:
        save_safe_gif(rainbow_test(), a.dst, delay_ms=120)
