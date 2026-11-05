"""Render the DevKit demo video from real captured command output.

python demo/render.py [--preview N]   # --preview renders only scene N as a PNG
Output: demo/out/devkit-demo.mp4 (1920x1080, 30 fps, silent, burned-in captions)
Needs ffmpeg on PATH, Pillow, and the Inter + DejaVu Sans Mono fonts (paths below).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
CAP = HERE / "captures"
OUT = HERE / "out"
W, H, FPS = 1920, 1080, 30
HOLD_SCALE = 1.2  # reading time per caption

# Cosmetic only: show the demo workspace as ~/demo and its DevKit home as ~/.devkit.
_WORK = str(Path(os.environ.get("DEVKIT_DEMO_WORK", HERE / ".work")).resolve())
PATH_REWRITES = [(_WORK + "/devkit-home", "~/.devkit"), (_WORK, "~/demo")]

# ── look ─────────────────────────────────────────────────────────────────────
BG = (11, 16, 32)
TERM_BG = (16, 21, 31)
TITLE_BG = (27, 34, 48)
FG = (214, 222, 235)
DIM = (120, 132, 150)
PROMPT = (110, 200, 140)
ACCENT = (86, 156, 255)
CAPTION_BG = (40, 44, 56, 225)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
INTER = "/usr/share/fonts/opentype/inter/Inter-Regular.otf"
INTER_M = "/usr/share/fonts/opentype/inter/Inter-Medium.otf"
INTER_SB = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
INTER_B = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"

F_MONO = ImageFont.truetype(MONO, 26)
F_MONO_B = ImageFont.truetype(MONO_B, 26)
F_CAPTION = ImageFont.truetype(INTER_M, 31)
F_WINTITLE = ImageFont.truetype(INTER, 17)
F_CHIP = ImageFont.truetype(INTER_SB, 17)

CHAR_W = F_MONO.getlength("M")
LINE_H = 35
TX, TY, TW, TH = 90, 46, W - 180, 866          # terminal window
TEXT_X, TEXT_Y = TX + 26, TY + 36 + 18
ROWS = (TH - 36 - 30) // LINE_H

ANSI16 = {
    30: (60, 66, 80), 31: (239, 99, 99), 32: (110, 200, 140), 33: (229, 192, 100),
    34: (97, 155, 255), 35: (198, 120, 221), 36: (86, 182, 194), 37: (214, 222, 235),
    90: (120, 132, 150), 91: (255, 123, 114), 92: (126, 231, 135), 93: (240, 210, 120),
    94: (121, 184, 255), 95: (210, 168, 255), 96: (118, 227, 234), 97: (255, 255, 255),
}


def xterm256(n: int) -> tuple[int, int, int]:
    if n < 16:
        return ANSI16[(30 + n) if n < 8 else (82 + n)]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n // 6) % 6, n % 6
        conv = lambda v: 0 if v == 0 else 55 + v * 40  # noqa: E731
        return conv(r), conv(g), conv(b)
    v = 8 + (n - 232) * 10
    return v, v, v


@dataclass
class Span:
    text: str
    fg: tuple = FG
    bold: bool = False


SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESC = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse_ansi(text: str) -> list[list[Span]]:
    """ANSI SGR -> lines of colored spans."""
    lines: list[list[Span]] = [[]]
    fg, bold, dim = FG, False, False
    pos = 0
    text = text.replace("\r\n", "\n")
    for m in SGR.finditer(text):
        chunk = OTHER_ESC.sub("", text[pos:m.start()])
        _emit(lines, chunk, DIM if dim and fg == FG else fg, bold)
        pos = m.end()
        params = [int(p) if p else 0 for p in m.group(1).split(";")] if m.group(1) else [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                fg, bold, dim = FG, False, False
            elif p == 1:
                bold = True
            elif p == 2:
                dim = True
            elif p == 22:
                bold = dim = False
            elif p in ANSI16:
                fg = ANSI16[p]
            elif p == 39:
                fg = FG
            elif p == 38 and i + 1 < len(params):
                if params[i + 1] == 5 and i + 2 < len(params):
                    fg = xterm256(params[i + 2]); i += 2
                elif params[i + 1] == 2 and i + 4 < len(params):
                    fg = tuple(params[i + 2:i + 5]); i += 4
            i += 1
    _emit(lines, OTHER_ESC.sub("", text[pos:]), DIM if dim and fg == FG else fg, bold)
    if lines and not lines[-1]:
        lines.pop()
    return lines


def _emit(lines, chunk, fg, bold):
    for k, part in enumerate(chunk.split("\n")):
        if k:
            lines.append([])
        if part:
            lines[-1].append(Span(part, fg, bold))


def rewrite(s: str) -> str:
    for a, b in PATH_REWRITES:
        s = s.replace(a, b)
    return s


def wrap_spans(line: list[Span], cols: int) -> list[list[Span]]:
    """Word-aware wrap: break at the last space before `cols` (hard break only if none)."""
    chars = [(ch, sp.fg, sp.bold) for sp in line for ch in sp.text]
    rows: list[list[tuple]] = []
    while len(chars) > cols:
        cut = max((i for i in range(cols) if chars[i][0] == " "), default=-1)
        if cut < cols // 3:
            rows.append(chars[:cols]); chars = chars[cols:]
        else:
            rows.append(chars[:cut]); chars = chars[cut + 1:]
    rows.append(chars)
    out = []
    for row in rows:
        spans: list[Span] = []
        for ch, fg, bold in row:
            if spans and spans[-1].fg == fg and spans[-1].bold == bold:
                spans[-1].text += ch
            else:
                spans.append(Span(ch, fg, bold))
        out.append(spans)
    return out


COLS = int((TW - 52) // CHAR_W)


# ── drawing ──────────────────────────────────────────────────────────────────
def wrap_text(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_caption(img: Image.Image, caption: str) -> None:
    if not caption:
        return
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    lines = wrap_text(d, caption, F_CAPTION, 1460)
    lh = 42
    widths = [d.textlength(l, font=F_CAPTION) for l in lines]
    bw = max(widths) + 44
    bh = lh * len(lines) + 22
    x0 = (W - bw) / 2
    y0 = H - 46 - bh
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=10, fill=CAPTION_BG)
    for i, l in enumerate(lines):
        d.text(((W - widths[i]) / 2, y0 + 10 + i * lh), l, font=F_CAPTION, fill=(255, 255, 255))
    img.alpha_composite(overlay)


def draw_terminal(lines: list[list[Span]], title: str, chip: str | None, caption: str,
                  cursor: bool) -> Image.Image:
    img = Image.new("RGBA", (W, H), BG + (255,))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([TX, TY, TX + TW, TY + TH], radius=14, fill=TERM_BG)
    d.rounded_rectangle([TX, TY, TX + TW, TY + 36], radius=14, fill=TITLE_BG)
    d.rectangle([TX, TY + 22, TX + TW, TY + 36], fill=TITLE_BG)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([TX + 18 + i * 22, TY + 12, TX + 30 + i * 22, TY + 24], fill=c)
    tw = d.textlength(title, font=F_WINTITLE)
    d.text((TX + (TW - tw) / 2, TY + 8), title, font=F_WINTITLE, fill=DIM)
    if chip:
        cw = d.textlength(chip, font=F_CHIP) + 24
        d.rounded_rectangle([TX + TW - cw - 16, TY + 6, TX + TW - 16, TY + 30], radius=12, fill=(70, 55, 20))
        d.text((TX + TW - cw - 4, TY + 9), chip, font=F_CHIP, fill=(245, 200, 90))
    lines = [w for line in lines for w in (wrap_spans(line, COLS)
             if sum(len(sp.text) for sp in line) > COLS else [line])]
    y = TEXT_Y
    for li, line in enumerate(lines[-ROWS:] if len(lines) > ROWS else lines):
        x = TEXT_X
        for sp in line:
            f = F_MONO_B if sp.bold else F_MONO
            d.text((x, y), sp.text, font=f, fill=sp.fg)
            x += CHAR_W * len(sp.text)
        if cursor and li == min(len(lines), ROWS) - 1:
            d.rectangle([x + 2, y + 3, x + CHAR_W, y + LINE_H - 4], fill=FG)
        y += LINE_H
    draw_caption(img, caption)
    return img


def draw_card(eyebrow: str, title: str, bullets: list[str], foot: str, caption: str,
              title_size: int = 58) -> Image.Image:
    img = Image.new("RGBA", (W, H), BG + (255,))
    d = ImageDraw.Draw(img)
    x, y = 200, 250
    d.text((x, y), eyebrow.upper(), font=ImageFont.truetype(INTER_SB, 22), fill=ACCENT)
    y += 50
    ft = ImageFont.truetype(INTER_B, title_size)
    for line in wrap_text(d, title, ft, 1500):
        d.text((x, y), line, font=ft, fill=(255, 255, 255)); y += title_size + 14
    y += 18
    fb = ImageFont.truetype(INTER, 30)
    for b in bullets:
        for k, line in enumerate(wrap_text(d, b, fb, 1440)):
            d.text((x + (0 if k else 0), y), ("•  " if k == 0 else "    ") + line, font=fb, fill=(205, 214, 228))
            y += 44
    y += 22
    d.text((x, y), foot, font=ImageFont.truetype(INTER, 24), fill=DIM)
    draw_caption(img, caption)
    return img


# ── timeline ─────────────────────────────────────────────────────────────────
@dataclass
class Beat:
    caption: str
    hold: float
    scroll_to: str | None = None     # regex: scroll so the first matching line is at the top
    tail: bool = False               # show the end of the buffer


@dataclass
class TermScene:
    capture: str
    title: str
    beats: list[Beat]
    steps: list[int] | None = None
    prompt: str = "acme-billing $ "
    id_sub: bool = True
    clip: dict[int, tuple[int, int]] = field(default_factory=dict)  # step -> (first, last) output lines


@dataclass
class CardScene:
    eyebrow: str
    title: str
    bullets: list[str]
    foot: str
    caption: str
    hold: float
    title_size: int = 58


def frames_for_term(sc: TermScene):
    data = json.loads((CAP / f"{sc.capture}.json").read_text())
    steps = data["steps"] if sc.steps is None else [data["steps"][i] for i in sc.steps]
    ids = re.findall(r"\b([0-9a-f]{6})\b", " ".join(s["output"] for s in data["steps"]))
    buf: list[list[Span]] = []
    caption = sc.beats[0].caption
    for si, st in enumerate(steps):
        cmd = st["display"]
        if sc.id_sub and "<id>" in cmd and ids:
            cmd = cmd.replace("<id>", ids[-1] if "recover" in sc.capture else ids[0])
        typed_line = [Span(sc.prompt, PROMPT, True)]
        buf.append(typed_line)
        n = len(cmd)
        type_secs = min(1.5, max(0.5, n / 60))
        for f in range(int(type_secs * FPS)):
            k = int(n * (f + 1) / (type_secs * FPS))
            buf[-1] = typed_line + [Span(cmd[:k], FG, True)]
            yield draw_terminal(buf, sc.title, None, caption, True), 1
        buf[-1:] = wrap_spans(typed_line + [Span(cmd, FG, True)], COLS)
        yield draw_terminal(buf, sc.title, None, caption, False), int(0.25 * FPS)
        secs = st["seconds"]
        if secs >= 4:  # long wait: show a labelled, sped-up spinner
            chip = f"sped up - real run {secs / 60:.1f} min" if secs >= 120 else f"sped up - real run {secs:.0f}s"
            for f in range(int(1.1 * FPS)):
                spin = "|/-\\"[(f // 3) % 4]
                frame_buf = buf + [[Span(f"{spin} running... {secs * (f + 1) / (1.1 * FPS):4.1f}s", DIM)]]
                yield draw_terminal(frame_buf, sc.title, chip, caption, False), 1
        out_lines = []
        for line in parse_ansi(rewrite(st["output"])):
            out_lines.extend(wrap_spans(line, COLS))
        if si in sc.clip:
            a, b = sc.clip[si]
            out_lines = out_lines[a:b]
        reveal = max(1, int(min(0.6, len(out_lines) / 90) * FPS))
        for f in range(reveal):
            k = int(len(out_lines) * (f + 1) / reveal)
            yield draw_terminal(buf + out_lines[:k], sc.title, None, caption, False), 1
        buf += out_lines
        if si < len(steps) - 1:
            yield draw_terminal(buf, sc.title, None, caption, False), int(0.5 * FPS)
    # beats: captions + optional scrolling over the finished buffer
    view_top = max(0, len(buf) - ROWS)
    for bi, beat in enumerate(sc.beats):
        target = view_top
        if beat.scroll_to:
            rx = re.compile(beat.scroll_to)
            for i, line in enumerate(buf):
                if rx.search("".join(s.text for s in line)):
                    target = min(i, max(0, len(buf) - ROWS)); break
        elif beat.tail:
            target = max(0, len(buf) - ROWS)
        if target != view_top:
            steps_n = int(0.5 * FPS)
            for f in range(steps_n):
                top = int(view_top + (target - view_top) * (f + 1) / steps_n)
                yield draw_terminal(buf[top:top + ROWS], sc.title, None, beat.caption, False), 1
            view_top = target
        yield draw_terminal(buf[view_top:view_top + ROWS], sc.title, None, beat.caption, False), int(beat.hold * HOLD_SCALE * FPS)


def frames_for_card(sc: CardScene):
    yield draw_card(sc.eyebrow, sc.title, sc.bullets, sc.foot, sc.caption, sc.title_size), int(sc.hold * FPS)


def render(scenes, path: Path, preview: int | None = None) -> float:
    OUT.mkdir(exist_ok=True)
    total = 0
    proc = None
    if preview is None:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE)
    for idx, sc in enumerate(scenes):
        if preview is not None and idx != preview:
            continue
        gen = frames_for_term(sc) if type(sc).__name__ == "TermScene" else frames_for_card(sc)
        last = None
        n_scene = 0
        for img, count in gen:
            if count <= 0:
                continue
            raw = img.convert("RGB").tobytes()
            if proc:
                for _ in range(count):
                    proc.stdin.write(raw)
            last = img
            n_scene += count
        total += n_scene
        if preview is not None and last is not None:
            last.convert("RGB").save(OUT / f"preview-{idx:02d}.png")
        print(f"scene {idx:2d} {n_scene / FPS:6.1f}s  (t={total / FPS:6.1f}s)", flush=True)
    if proc:
        proc.stdin.close()
        proc.wait()
    return total / FPS


if __name__ == "__main__":
    from scenes import SCENES
    if "--preview" in sys.argv:
        i = int(sys.argv[sys.argv.index("--preview") + 1])
        render(SCENES, OUT / "x.mp4", preview=i)
    else:
        secs = render(SCENES, OUT / "devkit-demo.mp4")
        print(f"total {secs:.1f}s")
