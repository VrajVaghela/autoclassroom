"""
Renders a terminal-style PNG of a command and its output.

Lab reports usually want "screenshots" of a program running. The AI can't
photograph the user's screen, so we draw the console output instead — either
the real captured output when code execution is enabled, or the model's
predicted output when it isn't.
"""

import os

from PIL import Image, ImageDraw, ImageFont

BG = (30, 30, 30)
CHROME = (55, 55, 58)
PROMPT_COLOR = (86, 214, 120)
TEXT_COLOR = (222, 222, 222)
TITLE_COLOR = (170, 170, 174)

PAD = 16
TITLEBAR = 30
LINE_H = 19
MAX_COLS = 96
MAX_LINES = 44


def _font():
    for name in ("consola.ttf", "DejaVuSansMono.ttf", "cour.ttf"):
        try:
            return ImageFont.truetype(name, 14)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text, width=MAX_COLS):
    lines = []
    for raw in (text or "").replace("\r\n", "\n").replace("\t", "    ").split("\n"):
        if not raw:
            lines.append("")
        while len(raw) > width:
            lines.append(raw[:width])
            raw = raw[width:]
        if raw:
            lines.append(raw)
    return lines


def render_terminal(path, command, output, title="Terminal"):
    """Draw command + output as a console window PNG. Returns the path."""
    font = _font()

    body = []
    if command:
        body.append(("prompt", f"$ {command}"))
    for line in _wrap(output):
        body.append(("text", line))
    if not body:
        body.append(("text", "(no output)"))

    truncated = len(body) > MAX_LINES
    if truncated:
        body = body[:MAX_LINES - 1] + [("text", f"... ({len(body) - MAX_LINES + 1} more lines)")]

    width = PAD * 2 + int(MAX_COLS * 8.2)
    height = TITLEBAR + PAD * 2 + LINE_H * len(body)

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, width, TITLEBAR], fill=CHROME)
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = 16 + i * 18
        draw.ellipse([cx - 6, TITLEBAR // 2 - 6, cx + 6, TITLEBAR // 2 + 6], fill=color)
    draw.text((78, TITLEBAR // 2 - 8), title, fill=TITLE_COLOR, font=font)

    y = TITLEBAR + PAD
    for kind, line in body:
        draw.text((PAD, y), line, fill=PROMPT_COLOR if kind == "prompt" else TEXT_COLOR, font=font)
        y += LINE_H

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, "PNG")
    return path
