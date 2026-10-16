#!/usr/bin/env python3
"""EMERGENCY FALLBACK ONLY — Generate GamePix-compliant assets with FREE AI image

generation (NO account needed). Use capture_assets.js FIRST: it produces assets
that are real captures of the game (impossible mismatch). AI generation guesses
what the game looks like from a text description and FAILED QA 3 times because
of it — only use this when no build is available to capture.

Method: anonymous Gradio API of Hugging Face Spaces running FLUX (Apache-2.0 / open).
A plain POST + SSE read gives an image URL; no signup, no API key. Validated Sept 2026.

Spaces tried in order (anonymous quota is per-IP and rolling — retry/backoff matters):
  1. black-forest-labs/FLUX.1-schnell   (fast, 4 steps)
  2. black-forest-labs/FLUX.1-dev       (slower, 28 steps)

Optional: set HF_TOKEN (env var) or put a free-account token in scripts/.hf_token
(git-ignored) to raise ZeroGPU anonymous quota. Get one free at
https://huggingface.co/settings/tokens (role: read is enough).

Outputs (in --out-dir):
  icon_256.png         256x256, <=1MB    (generated at 1024x1024, downscaled)
  cover_1360x850.png   1360x850, <=1.5MB (generated at native size)

Rules enforced:
  - NO text on assets: prompts ban text/letters/logos/watermarks; --prompt must be a
    VISUAL description only (never ask it to draw the game title).
  - Exact GamePix dimensions + byte limits (icon <=1MB, cover <=1.5MB); if a PNG
    exceeds the limit it is re-encoded as JPEG (also accepted by GamePix).
  - If all AI attempts fail (space down, quota exhausted, network), falls back to the
    PIL-drawn text-free assets from make_assets.py, and writes ASSETS_SOURCE=assets_pil_fallback.
  - BOTH non-capture asset sources are blocked at Submit by publish.js: the accepted
    provenance is assets_gameplay_capture (scripts/capture_assets.js) — real screenshots
    of the build being submitted. AI assets were rejected 3 times by GamePix QA because
    they did not correspond to the tested game.

Usage:
  pip install pillow   # only for post-processing + fallback
  python3 gen_assets.py --prompt "a cartoon moving truck loaded with cardboard boxes \
driving up a sunny hilly road toward a new house, vibrant colors, clean vector style" \
    --out-dir ./assets
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error


def _load_token() -> str:
    """HF token (raises ZeroGPU anonymous quota; free account, no payment needed).

    Sources, in order: HF_TOKEN env var, then a git-ignored scripts/.hf_token file
    containing just the token (never commit the raw token to a public repo —
    Hugging Face secret-scanning auto-revokes leaked tokens).
    """
    tok = os.environ.get("HF_TOKEN", "").strip()
    if tok:
        return tok
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".hf_token")
    for p in (here, os.path.expanduser("~/.hf_token")):
        try:
            if os.path.isfile(p):
                with open(p) as f:
                    tok = f.read().strip()
                if tok:
                    return tok
        except OSError:
            pass
    return ""


TOKEN = _load_token()

SPACES = [
    {
        "name": "FLUX.1-schnell",
        "host": "black-forest-labs-flux-1-schnell.hf.space",
        "steps": 4,
        "extra": [],           # [prompt, seed, rand, w, h, steps]
        "timeout": 300,
    },
    {
        "name": "FLUX.1-dev",
        "host": "black-forest-labs-flux-1-dev.hf.space",
        "steps": 28,
        "extra": [3.5],        # [prompt, seed, rand, w, h, guidance, steps]
        "timeout": 600,
    },
]

# Banned-content suffix: GamePix assets must carry NO text/names/branding.
# QA ALSO REJECTS rounded-corner icons: the artwork must fill the frame
# edge-to-edge ("full bleed", hard 90° corners). Saying "app icon style" makes
# FLUX draw an iOS squircle — a real QA rejection.
#
# NOTE: AI generation is now the FALLBACK path only. The primary pipeline is
# capture_assets.js (real gameplay captures of the very build submitted) —
# QA rejected games whose AI assets "did not correspond to the game tested".
NO_TEXT = ("no text, no letters, no words, no numbers, no typography, no logo, "
           "no watermark, no signature, no rounded corners, no iOS app icon shape, "
           "no squircle, no frame, no border, artwork fills the entire image "
           "edge to edge with hard square corners")

ICON_PROMPT = ("full-bleed square illustration, edge-to-edge composition: {p}, "
               "single centered subject, simple uncluttered background, bold shapes, "
               "vibrant colors, high contrast, {n}")
COVER_PROMPT = ("wide landscape game cover art, full-bleed edge-to-edge: {p}, "
                "dynamic full scene, detailed background, cinematic lighting, "
                "vibrant colors, {n}")

ICON_MAX = 1 * 1024 * 1024      # 1MB
COVER_MAX = 1536 * 1024         # 1.5MB

# Provenance marker written next to the assets (publish.js refuses to submit on the PIL value).
AI_MARKER = "ASSETS_SOURCE"
ASSET_SOURCE_AI = "assets_ai_generated"     # real FLUX output — safe to submit
ASSET_SOURCE_PIL = "assets_pil_fallback"    # PIL placeholder — NEVER submit


def write_source_marker(out_dir: str, ai: bool) -> None:
    """Persist whether assets are AI-generated or the PIL fallback."""
    with open(os.path.join(out_dir, AI_MARKER), "w") as f:
        f.write((ASSET_SOURCE_AI if ai else ASSET_SOURCE_PIL) + "\n")


def _headers(extra: dict | None = None) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    if extra:
        h.update(extra)
    return h


def post_call(space: dict, prompt: str, width: int, height: int, seed: int) -> str:
    data = [prompt, seed, seed < 0, width, height, *space["extra"], space["steps"]]
    payload = json.dumps({"data": data}).encode()
    req = urllib.request.Request(
        f"https://{space['host']}/gradio_api/call/infer", data=payload,
        headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    event_id = body.get("event_id")
    if not event_id:
        raise RuntimeError(f"no event_id: {body}")
    return event_id


def wait_result(space: dict, event_id: str) -> str:
    url = f"https://{space['host']}/gradio_api/call/infer/{event_id}"
    with urllib.request.urlopen(url, timeout=space["timeout"]) as r:
        ev = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n\r")
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: ") and ev == "complete":
                data = json.loads(line[6:])
                if not data or not data[0]:
                    raise RuntimeError("empty result (busy or quota) — retry")
                return data[0]["url"]
            elif line.startswith("data: ") and ev == "error":
                raise RuntimeError(f"generation error: {line[6:][:150]}")
    raise RuntimeError("SSE ended without result")


def download(url: str, out: str) -> None:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
        f.write(r.read())


def pollinations_image(prompt: str, width: int, height: int, seed: int, out: str) -> str:
    """Keyless FLUX endpoint (pollinations.ai) — backup AI provider when the
    HF Spaces GPU quota is exhausted. Still REAL AI output (assets_ai_generated)."""
    from urllib.parse import quote
    from PIL import Image
    import io
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
           f"?width={width}&height={height}&seed={seed}&model=flux&nologo=true")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    img = Image.open(io.BytesIO(data))
    img.verify()
    if len(data) < 4096:
        raise RuntimeError("pollinations returned a suspiciously small image")
    with open(out, "wb") as f:
        f.write(data)
    return out


def gen_image(prompt: str, width: int, height: int, seed: int,
              retries: int = 4, backoff: int = 20) -> str:
    """Try every space, with exponential backoff (anonymous GPU quota is rolling)."""
    last = None
    for i in range(retries):
        for space in SPACES:
            try:
                eid = post_call(space, prompt, width, height, seed + i)
                return wait_result(space, eid)
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"  try {i+1}/{retries} via {space['name']}: {e}")
        if i < retries - 1:
            wait = backoff * (i + 1)
            print(f"  all spaces busy — waiting {wait}s (quota is rolling)...")
            time.sleep(wait)
    raise RuntimeError(f"AI generation failed after {retries} rounds: {last}")


def fit(path_in: str, path_out: str, w: int, h: int, max_bytes: int) -> str:
    """Exact-size cover-crop + resize -> PNG; if over byte limit, re-encode as JPEG."""
    from PIL import Image
    img = Image.open(path_in).convert("RGB")
    sw, sh = img.size
    ta, sa = w / h, sw / sh
    if sa > ta:  # too wide
        nw = int(sh * ta)
        img = img.crop(((sw - nw) // 2, 0, (sw + nw) // 2, sh))
    else:        # too tall
        nh = int(sw / ta)
        img = img.crop((0, (sh - nh) // 2, sw, (sh + nh) // 2))
    img = img.resize((w, h), Image.LANCZOS)
    img.save(path_out, optimize=True)
    if os.path.getsize(path_out) > max_bytes:
        img.save(path_out, "JPEG", quality=88, optimize=True)
    size = os.path.getsize(path_out)
    if size > max_bytes:
        raise RuntimeError(f"{path_out} is {size} bytes (> {max_bytes})")
    return path_out


def corners_are_square(path: str, probe: int = 6, tol: int = 40) -> bool:
    """QA rule: corners must be FULL and square (no rounded crop, no squircle).
    Probe corner pixels: opaque, and pixel-continuous with adjacent edge pixels."""
    from PIL import Image
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


def fallback_pil(out_dir: str) -> tuple[str, str]:
    """Last-resort text-free assets so publishing never blocks (make_assets.py)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import make_assets  # noqa: PLC0415
    icon = os.path.join(out_dir, "icon_256.png")
    cover = os.path.join(out_dir, "cover_1360x850.png")
    make_assets.make_icon(None, icon)      # title=None -> no text, by design
    make_assets.make_cover(None, cover)
    return icon, cover


def main() -> None:
    ap = argparse.ArgumentParser(description="AI game assets (icon+cover), no account needed")
    ap.add_argument("--prompt", required=True,
                    help="VISUAL description of the game (no text to render!)")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--retries", type=int, default=4, help="rounds over all spaces")
    ap.add_argument("--no-fallback", action="store_true",
                    help="fail instead of drawing PIL fallback assets")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    icon_p = ICON_PROMPT.format(p=args.prompt, n=NO_TEXT)
    cover_p = COVER_PROMPT.format(p=args.prompt, n=NO_TEXT)
    tmp = os.path.join(args.out_dir, ".gen_tmp")

    ai_ok = False
    try:
        print("[1/4] Generating icon (1024x1024)...")
        # up to 3 rounds with fresh seeds: rounded/cut corners are auto-rejected (QA)
        icon = None
        for attempt in range(3):
            seed = args.seed + attempt * 7919
            try:
                url = gen_image(icon_p, 1024, 1024, seed, args.retries)
                download(url, tmp)
            except Exception as hf_err:  # noqa: BLE001
                print(f"  HF Spaces unavailable ({hf_err}) — trying Pollinations (FLUX, keyless)...")
                pollinations_image(icon_p, 1024, 1024, seed, tmp)
            cand = fit(tmp, os.path.join(args.out_dir, "icon_256.png"), 256, 256, ICON_MAX)
            if corners_are_square(cand):
                icon = cand
                break
            print(f"  attempt {attempt + 1}/3: rounded/cut corners detected — regenerating with a new seed (QA auto-reject)")
        if icon is None:
            raise RuntimeError("icon corners are rounded/cut after 3 attempts (QA auto-reject)")

        print("[2/4] Generating cover (1360x850)...")
        try:
            url = gen_image(cover_p, 1360, 850, args.seed + 100, args.retries)
            download(url, tmp)
        except Exception as hf_err:  # noqa: BLE001
            print(f"  HF Spaces unavailable ({hf_err}) — trying Pollinations (FLUX, keyless)...")
            pollinations_image(cover_p, 1360, 850, args.seed + 100, tmp)
        cover = fit(tmp, os.path.join(args.out_dir, "cover_1360x850.png"), 1360, 850, COVER_MAX)
        ai_ok = True
    except Exception as e:  # noqa: BLE001
        if args.no_fallback:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        print(f"⚠️  AI generation unavailable ({e}) — using PIL fallback assets.")
        icon, cover = fallback_pil(args.out_dir)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    write_source_marker(args.out_dir, ai_ok)
    print(f"[3/4] icon  : {icon}  ({os.path.getsize(icon)} bytes, 256x256)")
    print(f"[4/4] cover : {cover}  ({os.path.getsize(cover)} bytes, 1360x850)")
    if ai_ok:
        print("✅ Done — AI-generated, text-free, square corners (ASSETS_SOURCE=assets_ai_generated).")
        print("⚠️  publish.js BLOCKS Submit on AI assets: QA rejected 3 games whose AI assets")
        print("⚠️  did not match the tested game. Prefer real gameplay captures:")
        print("⚠️    xvfb-run -a node scripts/capture_assets.js --zip build.zip --out-dir <dir>")
    else:
        print("⚠️  Done, but these are PIL FALLBACK assets (ASSETS_SOURCE=assets_pil_fallback).")
        print("⚠️  NEITHER fallback NOR AI assets may be submitted: produce real gameplay")
        print("⚠️  captures of the very build submitted (capture_assets.js) before Submit.")


if __name__ == "__main__":
    main()
