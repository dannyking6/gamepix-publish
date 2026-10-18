# gamepix-publish

AI-agent skill to **integrate the GamePix SDK** into any HTML5 game and **publish it end-to-end** on the GamePix developer dashboard — create game → Info → Assets → Editorial → Build → **Submit for Review**, 100% automated with Playwright.

Validated end-to-end (Sept 2026): game created, assets uploaded, build processed `ready`, status flipped to `review`.

## Install

```bash
npm i playwright && npx playwright install chromium
# Linux: xvfb for headed mode (Cloudflare)
sudo apt-get install -y xvfb   # or use xvfb-run from your distro
```

## Use as an agent skill

Point your agent (Claude Code, Codebuff, ...) at `SKILL.md` in this repo, e.g. with [npx skills](https://www.npmjs.com/package/skills):

```bash
npx skills add <owner>/gamepix-publish --skill gamepix-publish --yes
```

The skill instructions (`SKILL.md`) contain the full platform knowledge: Cloudflare bypass, Ionic/Angular interaction rules, exact selectors, API confirmation signals, asset specs and compliance checklist.

## Use as a one-shot CLI

```bash
export GPX_EMAIL=you@example.com
export GPX_PASSWORD='secret'
export GPX_TITLE="My Game"
export GPX_MAIN_TAG="puzzle"            # main tag (puzzle, arcade, racing, ...)
export GPX_EXTRA_TAGS="driving,simulation"  # secondary tags — REQUIRED by this workflow
export GPX_DESCRIPTION="Original 100-500 char description of the game..."
export GPX_HOW_TO_PLAY="Desktop
Arrows = Move

Mobile
Swipe = Move"
export GPX_ICON=./icon_256.png
export GPX_COVER=./cover_1360x850.png
export GPX_ZIP=./my-game-v1.0.0.zip
export GPX_RELEASE_NOTES="First release."

xvfb-run -a node scripts/publish.js
# exit 0  = submitted for review
# exit 2  = everything completed EXCEPT submit — assets were not AI-generated
#           (never submit a game with self-made/fallback assets)
```

Generate compliant assets by **capturing REAL GAMEPLAY of the very build you submit** (QA rejected 3 games whose AI assets did not match the tested game):

```bash
pip install pillow
xvfb-run -a node scripts/capture_assets.js --zip my-game-v1.0.0.zip --out-dir ./assets --title "My Game"
# -> assets/icon_256.png (256x256, <=1MB) + assets/cover_1360x850.png (1360x850, <=1.5MB)
```

- The build is auto-started and given a settle window — **menu/loading frames are never used**: flat frames and HUD-text-heavy frames are dropped by the post-process.
- Crops are **full-bleed with square corners** (rounded-corner icons are a QA auto-reject), pixel-probed on all 4 corners.
- `--dir <unpacked-build>`, `--settle`, `--frames`, `--interval`, `--width/--height` (for portrait games) tune the capture.
- The script writes `ASSETS_SOURCE=assets_gameplay_capture`, the ONLY provenance `publish.js` accepts at Submit.
- `scripts/gen_assets.py` (FLUX AI) remains as an **emergency fallback** when no build exists to capture — its output is blocked at Submit (`assets_ai_generated`), same as PIL fallbacks.

## What the script verifies

(plus the pre-QA gates: SDK-as-first-script in the zipped `index.html` before the `sdkIntegration` flag is set / Submit; assets accepted only with `ASSETS_SOURCE=assets_gameplay_capture`)

- `PUT api.gamepix.com/v3/devs/game-drafts/<ns>` 200 — form saved
- `POST api.gamepix.com/v3/devs/game-drafts/upload/asset` 200 ×2 — icon + cover
- S3 `PUT gpx-mygamepix-builds...zip` 200 — build uploaded, then "Build ready, your new version code is: X"
- `PUT api.gamepix.com/v3/devs/game-drafts/<ns>/review` 200 — submitted
- Draft state: `"status":"review"`, `"buildStatus":"ready"`, `"buildMessage":"The build is reviewable"`

## Files

- `SKILL.md` — the actual skill (platform knowledge + step-by-step)
- `scripts/publish.js` — one-shot end-to-end publisher (**blocks Submit for review** unless assets are proven gameplay captures of this build AND the SDK is verifiably integrated in the zip — exit code 2)
- `scripts/capture_assets.js` — launches the REAL build, auto-starts past menus, captures gameplay frames (needs playwright + xvfb)
- `scripts/capture_postprocess.py` — picks the most action-rich frames, full-bleed square-corner crops, exact GamePix specs, writes `ASSETS_SOURCE=assets_gameplay_capture`
- `scripts/bridges/poki_bridge.js` — AD BRIDGE: PokiSDK games get REAL GamePix ads (commercialBreak→interstitialAd, rewardedBreak→rewardAd, happyTime→happyMoment) **+ audio-safe shim: bridges themselves suspend all AudioContexts and pause audio/video elements during every ad break**
- `scripts/bridges/snacks_bridge.js` — AD BRIDGE: GameSnacks games get REAL GamePix ads (ad.break next/reward→interstitial/reward, score.update→updateScore, levelComplete→updateLevel+happyMoment, game.ready→loaded) **+ same audio-safe shim**
- `scripts/gen_assets.py` — FALLBACK ONLY: FREE AI asset generator (FLUX via anonymous HF Spaces API, full-bleed prompts + square-corner check, writes the `ASSETS_SOURCE` provenance marker) — output blocked at Submit
- `scripts/make_assets.py` — offline PIL fallback (renders the game title on the assets; draft completion only — never submit with these)
- `scripts/verify_clean.js` — verifies a build is fully cleaned of its origin: no source platform name (Poki, Softgames, Construct, Phaser, PixiJS, Unity, Godot, Famobi, …) and no original game name may appear in any shipped file (contents or file names). Exits 0 only on a clean build; run before submit (`npm run verify-clean -- <build-dir> <platform> <original-name>`).

## REVIEW LOCK (important)

Once a game is submitted, the build is **server-locked** until QA answers: `build-signed-url` returns 401 "Unauthorized game state" while in review, status cannot be flipped back to edit, and there is no withdraw/delete. Therefore ALWAYS integrate the SDK **and the ad bridge** (and score/level events) BEFORE the first submission. See SKILL.md §7 for the full evidence and `--upload` watcher pattern for auto-upgrading builds when QA unlocks a game.

## Disclaimer

Use with an account you own. GamePix QA reviews every submission; games must comply with https://partners.gamepix.com/guidelines/submission (no external links/analytics, relative paths, pause during ads, etc.).
