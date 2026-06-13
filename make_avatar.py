# -*- coding: utf-8 -*-
"""
Генератор стильних аватарок для бота-анкети косметолога.
Створює кілька варіантів у папці avatar/ (PNG 640x640, придатні для @BotFather → setuserpic).

Запуск:  python make_avatar.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("avatar")
OUT.mkdir(exist_ok=True)

SCALE = 2          # рендеримо у 2× і зменшуємо — для гладких країв
SIZE = 640
S = SIZE * SCALE

FONTS = "C:/Windows/Fonts/"
SERIF = FONTS + "constanb.ttf"     # Constantia Bold — елегантний сериф
SERIF2 = FONTS + "georgiab.ttf"
SANS = FONTS + "segoeui.ttf"
SANS_L = FONTS + "segoeuil.ttf"    # Segoe UI Light


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(top, bottom):
    img = Image.new("RGB", (S, S), top)
    px = img.load()
    for y in range(S):
        c = lerp(top, bottom, y / (S - 1))
        for x in range(S):
            px[x, y] = c
    return img


def radial_glow(img, center=(0.5, 0.38), strength=42, radius=0.75):
    """Додає м'яке світло у верхній частині — об'ємність."""
    glow = Image.new("RGB", (S, S), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    cx, cy = int(S * center[0]), int(S * center[1])
    R = int(S * radius)
    steps = 60
    for i in range(steps, 0, -1):
        r = int(R * i / steps)
        v = int(strength * (1 - i / steps))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(v, v, v))
    return Image.blend(img, Image.eval(img, lambda p: p), 0) if False else \
        Image.fromarray(_add(img, glow))


def _add(a, b):
    import numpy as np
    return np.clip(np.asarray(a, int) + np.asarray(b, int), 0, 255).astype("uint8")


def star4(d, cx, cy, R, fill):
    r = R * 0.30
    pts = [(cx, cy - R), (cx + r, cy - r), (cx + R, cy), (cx + r, cy + r),
           (cx, cy + R), (cx - r, cy + r), (cx - R, cy), (cx - r, cy - r)]
    d.polygon(pts, fill=fill)


def spaced_text(d, cy, text, fnt, fill, spacing):
    """Малює текст по центру горизонталі з міжлітерним інтервалом."""
    widths = [d.textbbox((0, 0), ch, font=fnt)[2] for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = (S - total) / 2
    for ch, w in zip(text, widths):
        d.text((x, cy), ch, font=fnt, fill=fill)
        x += w + spacing


def make(name, top, bottom, ring, mono_color, sub_color, glow=True):
    img = gradient(top, bottom)
    if glow:
        try:
            img = radial_glow(img)
        except Exception:
            pass
    d = ImageDraw.Draw(img)

    # блискітки
    star4(d, int(S * 0.78), int(S * 0.22), int(S * 0.045), sub_color)
    star4(d, int(S * 0.83), int(S * 0.30), int(S * 0.022), sub_color)
    star4(d, int(S * 0.20), int(S * 0.74), int(S * 0.030), sub_color)

    # тонке кільце
    margin = int(S * 0.085)
    d.ellipse([margin, margin, S - margin, S - margin],
              outline=ring, width=max(2, int(S * 0.006)))

    # монограма
    fmono = font(SERIF, int(S * 0.36))
    text = "АЛ"
    bb = d.textbbox((0, 0), text, font=fmono)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((S - tw) / 2 - bb[0], S * 0.40 - th / 2 - bb[1]),
           text, font=fmono, fill=mono_color)

    # розділова рисочка
    lw = int(S * 0.16)
    ly = int(S * 0.60)
    d.line([(S / 2 - lw / 2, ly), (S / 2 + lw / 2, ly)], fill=ring, width=max(1, int(S * 0.004)))

    # підпис
    fsub = font(SANS_L, int(S * 0.052))
    spaced_text(d, int(S * 0.635), "КОСМЕТОЛОГ", fsub, sub_color, int(S * 0.012))
    fname = font(SERIF2, int(S * 0.060))
    bb2 = d.textbbox((0, 0), "Анна Людвик", font=fname)
    d.text(((S - (bb2[2] - bb2[0])) / 2, int(S * 0.70)), "Анна Людвик", font=fname, fill=mono_color)

    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    path = OUT / f"avatar_{name}.png"
    img.save(path, "PNG")
    print("✅", path)


if __name__ == "__main__":
    # 1. Ніжний трояндовий (blush rose)
    make("1_blush", (248, 211, 214), (224, 150, 162),
         ring=(201, 162, 75), mono_color=(255, 255, 255), sub_color=(255, 248, 248))
    # 2. Нюд + золото (елегантний, дорогий)
    make("2_nude_gold", (240, 228, 211), (199, 161, 99),
         ring=(120, 86, 45), mono_color=(94, 62, 35), sub_color=(120, 86, 45))
    # 3. Мокко / рожеве золото (глибокий, преміум)
    make("3_mauve", (181, 122, 134), (96, 58, 72),
         ring=(232, 201, 160), mono_color=(251, 239, 230), sub_color=(232, 201, 160))
    print("Готово. Дивіться файли у папці avatar/")
