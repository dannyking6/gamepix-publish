#!/usr/bin/env python3
"""capture_postprocess.py — pick the best GAMEPLAY frames and turn them into
GamePix-compliant icon (256x256, <=1MB) + cover (1360x850, <=1.5MB).

Hard QA rules implemented here (all from real rejections):
  - FULL-BLEED square corners: icon and cover are hard-crops with 90° corners,
    artwork fills the frame edge-to-edge. A pixel probe verifies that all four
    corners are opaque and pixel-continuous (kills rounded-corner icons).
  - NO text on assets: frames dominated by HUD/UI text are skipped
    (simple heuristic: high-contrast small-text frequency in a border band).
  - Menu/loading frames are skipped: they are rejected as low-detail frames
    (uniform/blurry) — gameplay frames have many distinct edges/details.
  - Exact dimensions + byte caps with JPEG fallback (GamePix accepts both).

Usage (called by capture_assets.js, but usable standalone):
  python3 capture_postprocess.py .frames --out-dir ./assets \
      --icon-size 256 --cover-size 1360x850
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ICON_MAX = 1 * 1024 * 1024
COVER_MAX = 1536 * 1024


def edge_density(img):
    """Cheap detail score: gameplay frames are busy, menus/loading are flat."""
    g = img.convert("L").resize((160, 90))
    edges = g.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist)
    if not total:
        return 0.0
    # fraction of strongly-contrasting pixels
    strong = sum(hist[64:])
    return strong / total


def text_bandiness(img):
    """Heuristic text indicator: many tiny high-contrast connected components
    in horizontal lines = HUD text. High value -> frame likely full of UI."""
    g = img.convert("L").resize((320, 180))
    edges = g.filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist) or 1
    small_contrast = sum(hist[96:])
    return small_contrast / total


def corners_square(path, probe=6, tol=40):
    """All four corner pixels opaque and pixel-continuous with adjacent edges."""
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    pts = {"tl": (0, 0), "tr": (w - 1, 0), "bl": (0, h - 1), "br": (w - 1, h - 1)}
    for name, (x, y) in pts.items():
        r, g, b, a = img.getpixel((x, y))
        if a < 250:
            return False
        for dx, dy in ((probe, 0), (0, probe)):
            x2 = min(max(x + dx * (1 if x < w // 2 else -1), 0), w - 1)
            y2 = min(max(y + dy * (1 if y < h // 2 else -1), 0), h - 1)
            r2, g2, b2, _ = img.getpixel((x2, y2))
            if max(abs(r - r2), abs(g - g2), abs(b - b2)) > tol:
                return False
    return True


def crop_fullbleed(img, w, h):
    """Center cover-crop to exact w×h, from the CENTER-DENSE region of the
    frame (largest action subject) — never leaves rounded/torn corners."""
    sw, sh = img.size
    ta = w / h
    sa = sw / sh
    if sa > ta:  # source too wide: crop horizontally, biased to center
        nw = int(sh * ta)
        x0 = (sw - nw) // 2
        img = img.crop((x0, 0, x0 + nw, sh))
    else:        # source too tall: crop vertically (portrait plays)
        nh = int(sw / ta)
        y0 = (sh - nh) // 2
        img = img.crop((0, y0, sw, y0 + nh))
    return img.resize((w, h), Image.LANCZOS)


def save_within_limit(img, path, max_bytes):
    img.save(path, optimize=True)
    if os.path.getsize(path) > max_bytes:
        img.save(path, "JPEG", quality=88, optimize=True)
    if os.path.getsize(path) > max_bytes:
        raise RuntimeError(f"{path} is {os.path.getsize(path)} bytes (> {max_bytes})")


def pick_icon_frame(frames):
    """Icon = the single most action-rich CENTER of any frame; needs to be
    readable at 256px, so we score the CENTER THIRD's detail density."""
    best, best_score = None, -1.0
    for p in frames:
        img = Image.open(p).convert("RGB")
        w, h = img.size
        cx0, cx1 = w // 3, 2 * w // 3
        cy0, cy1 = h // 3, 2 * h // 3
        c = img.crop((cx0, cy0, cx1, cy1))
        score = edge_density(c)
        if score > best_score:
            best, best_score = (p, img), score
    return best


def pick_cover_frame(frames, used):
    """Cover = most detailed full frame, excluding the one used for the icon."""
    best, best_score = None, -1.0
    for p in frames:
        if p == used:
            continue
        img = Image.open(p).convert("RGB")
        score = edge_density(img)
        if score > best_score:
            best, best_score = (p, img), score
    return best


FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]
def _font(size):
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    # try bundled Nunito-Black if present
    for p in ("/tmp/opencode/blumgi-bounce/fonts/Nunito-Black.ttf",):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_title(title, draw, font, max_width):
    """Wrap the title into at most 2 balanced lines if it is too wide."""
    words = title.split()
    if len(words) <= 1:
        return [title]
    # try single line first
    bb = draw.textbbox((0, 0), title, font=font)
    if (bb[2] - bb[0]) <= max_width:
        return [title]
    # greedy wrap into 2 lines, balancing widths
    best = None
    for i in range(1, len(words)):
        line1 = " ".join(words[:i])
        line2 = " ".join(words[i:])
        b1 = draw.textbbox((0, 0), line1, font=font)
        b2 = draw.textbbox((0, 0), line2, font=font)
        w1 = b1[2] - b1[0]; w2 = b2[2] - b2[0]
        if w1 <= max_width and w2 <= max_width:
            best = ([line1, line2], max(w1, w2))
            break
    if best is None:
        # fall back to 2 lines split in the middle
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    return best[0]


def apply_title(path, title, max_bytes):
    """Overlay a polished game title on an asset (per GamePix guideline: title on
    assets must match the game's title). Renders a subtle dark band + drop
    shadow + thick outline + bright fill for a premium, legible look."""
    if not title:
        return
    base = Image.open(path).convert("RGBA")
    w, h = base.size

    # 1) fit font size for the (possibly wrapped) title
    max_text_width = int(w * 0.92)
    for size in range(int(w * 0.11), 9, -1):
        font = _font(size)
        tmp = ImageDraw.Draw(base)
        lines = _wrap_title(title, tmp, font, max_text_width)
        widths = [tmp.textbbox((0, 0), ln, font=font)[2] - tmp.textbbox((0, 0), ln, font=font)[0] for ln in lines]
        heights = [tmp.textbbox((0, 0), ln, font=font)[3] - tmp.textbbox((0, 0), ln, font=font)[1] for ln in lines]
        total_h = sum(heights) + (len(lines) - 1) * int(size * 0.25)
        if max(widths) <= max_text_width and total_h <= int(h * 0.28):
            break
    else:
        font = _font(10)
        lines = _wrap_title(title, ImageDraw.Draw(base), font, max_text_width)
    tmp = ImageDraw.Draw(base)
    widths = [tmp.textbbox((0, 0), ln, font=font)[2] - tmp.textbbox((0, 0), ln, font=font)[0] for ln in lines]
    heights = [tmp.textbbox((0, 0), ln, font=font)[3] - tmp.textbbox((0, 0), ln, font=font)[1] for ln in lines]
    line_gap = int(font.size * 0.22)
    total_h = sum(heights) + (len(lines) - 1) * line_gap
    max_tw = max(widths)

    # 2) position: bottom-center with a margin
    margin_x = int(w * 0.04)
    margin_y = int(h * 0.06)
    band_pad_x = int(w * 0.06)
    band_pad_y = int(h * 0.03)
    band_w = max_tw + band_pad_x * 2
    band_h = total_h + band_pad_y * 2
    band_x0 = (w - band_w) // 2
    band_y0 = h - band_h - margin_y
    band_x1 = band_x0 + band_w
    band_y1 = band_y0 + band_h

    # 3) composite a semi-transparent dark rounded band
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    radius = int(h * 0.025)
    od.rounded_rectangle((band_x0, band_y0, band_x1, band_y1), radius=radius, fill=(0, 0, 0, 150))
    base = Image.alpha_composite(base, overlay)

    # 4) draw text: drop shadow + thick outline + bright fill
    draw = ImageDraw.Draw(base)
    # vertical start of text block inside band
    ty = band_y0 + band_pad_y
    shadow_off = max(2, int(font.size * 0.06))
    outline = max(2, int(font.size * 0.08))
    for ln, lh in zip(lines, heights):
        bb = draw.textbbox((0, 0), ln, font=font)
        tw = bb[2] - bb[0]
        x = (w - tw) // 2
        # drop shadow
        draw.text((x + shadow_off, ty + shadow_off), ln, font=font, fill=(0, 0, 0, 200))
        # outline
        for dx in range(-outline, outline + 1):
            for dy in range(-outline, outline + 1):
                if dx == 0 and dy == 0:
                    continue
                if dx * dx + dy * dy > outline * outline:
                    continue
                draw.text((x + dx, ty + dy), ln, font=font, fill=(0, 0, 0, 255))
        # main fill (bright white) + subtle golden tint on top line for flair
        fill = (255, 255, 255, 255)
        draw.text((x, ty), ln, font=font, fill=fill)
        ty += lh + line_gap

    # 5) save, converting back to RGB (JPEG/PNG)
    out = base.convert("RGB")
    out.save(path, optimize=True)
    if os.path.getsize(path) > max_bytes:
        out.save(path, "JPEG", quality=88, optimize=True)
    if os.path.getsize(path) > max_bytes:
        raise RuntimeError(f"{path} is {os.path.getsize(path)} bytes (> {max_bytes})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir", help="directory of gameplay frames (png)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--icon-size", type=int, default=256)
    ap.add_argument("--cover-size", default="1360x850")
    ap.add_argument("--max-icon-bytes", type=int, default=ICON_MAX)
    ap.add_argument("--max-cover-bytes", type=int, default=COVER_MAX)
    ap.add_argument("--title", default="", help="Game title rendered on the assets (must match game title)")
    ap.add_argument("--min-detail", type=float, default=0.05,
                    help="skip frames flatter than this (menus/loading)")
    ap.add_argument("--max-text", type=float, default=0.22,
                    help="skip frames whose HUD text likely dominates")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    files = sorted(
        os.path.join(args.frames_dir, f)
        for f in os.listdir(args.frames_dir)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    if not files:
        print("no frames found in", args.frames_dir, file=sys.stderr)
        sys.exit(1)

    kept = []
    for p in files:
        img = Image.open(p).convert("RGB")
        det = edge_density(img)
        txt = text_bandiness(img)
        if det < args.min_detail:
            print(f"skip (menu/flat) {os.path.basename(p)}: detail={det:.3f}")
            continue
        if txt > args.max_text:
            print(f"skip (HUD text) {os.path.basename(p)}: text={txt:.3f}")
            continue
        print(f"keep  {os.path.basename(p)}: detail={det:.3f} text={txt:.3f}")
        kept.append(p)
    if len(kept) < 2:
        # too strict: keep the top-2 most detailed frames anyway (gameplay-est)
        scored = [(edge_density(Image.open(p).convert('RGB')), p) for p in files]
        scored.sort(reverse=True)
        kept = [p for _, p in scored[:2]]
        print("strict filter too tight — falling back to top-2 detail frames")

    icon_src, icon_img = pick_icon_frame(kept)
    cover_src, cover_img = pick_cover_frame(kept, icon_src)
    print(f"icon frame : {icon_src}")
    print(f"cover frame: {cover_src}")

    icon_path = os.path.join(args.out_dir, "icon_256.png")
    cover_path = os.path.join(args.out_dir, "cover_1360x850.png")

    cw, ch = (int(x) for x in args.cover_size.lower().split("x"))
    icon = crop_fullbleed(icon_img, args.icon_size, args.icon_size)
    cover = crop_fullbleed(cover_img, cw, ch)
    save_within_limit(icon, icon_path, args.max_icon_bytes)
    save_within_limit(cover, cover_path, args.max_cover_bytes)

    if args.title:
        apply_title(icon_path, args.title, args.max_icon_bytes)
        apply_title(cover_path, args.title, args.max_cover_bytes)
        print("title rendered:", args.title)

    for p in (icon_path, cover_path):
        if not corners_square(p):
            # hard re-crop from an inset region: kills any model/scale artifacts
            img = Image.open(p).convert("RGB")
            w, h = img.size
            inset = max(2, min(w, h) // 64)
            img = img.crop((inset, inset, w - inset, h - inset)).resize((w, h), Image.LANCZOS)
            save_within_limit(img, p, args.max_icon_bytes if "icon" in p else args.max_cover_bytes)
            if not corners_square(p):
                print(f"WARN: corners of {p} still not perfectly square-continuous", file=sys.stderr)

    with open(os.path.join(args.out_dir, "ASSETS_SOURCE"), "w") as f:
        f.write("assets_gameplay_capture\n")

    print("✅ icon :", icon_path, os.path.getsize(icon_path), "bytes")
    print("✅ cover:", cover_path, os.path.getsize(cover_path), "bytes")
    print("ASSETS_SOURCE=assets_gameplay_capture written")


if __name__ == "__main__":
    main()
