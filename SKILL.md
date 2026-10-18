---
name: gamepix-publish
description: Integrate the GamePix SDK into any HTML5 game and publish it end-to-end on the GamePix developer dashboard (create game, fill Info/Assets/Editorial/Build tabs, upload ZIP, submit for review). Use when the user wants to "publish a game on GamePix", "integrate gamepix sdk", "submit a game to gamepix", or "release an html5 game".
---

# GamePix SDK Integration & Automated Publishing

This skill lets an AI agent take ANY HTML5 game and: (1) integrate the GamePix SDK v3 correctly, (2) package it into a compliant ZIP, and (3) publish it end-to-end on `my.gamepix.com` (dashboard) via Playwright — from login to "Submit for Review", without any human interaction.

Everything below was validated in a real end-to-end run (game created, assets uploaded, build processed `ready`, status `review`).

## 0. Prerequisites

- Node.js + Playwright (`npm i playwright` + `npx playwright install chromium`)
- `xvfb-run` available (Linux) — run the browser HEADED under Xvfb, never headless (Cloudflare)
- GamePix developer account: **email + password** (dashboard login is direct email/password at `https://my.gamepix.com/login`; the "Sign in with Xsolla" button on www.gamepix.com is NOT needed)
- User agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36`

## 1. Hard-won platform knowledge (read first!)

### Cloudflare traps
- `www.gamepix.com` is behind Cloudflare. **Headless is blocked.** Launch headed + `--disable-blink-features=AutomationControlled` under `xvfb-run -a`.
- Wait out "Just a moment..." by polling `page.title()` up to 45s.
- **Block trackers with `page.route()`** — this is what makes the SPA boot reliably:
  `page.route(/doubleclick|criteo|tiktok|clarity|googletagmanager|google-analytics|fundingchoices|adservice|gpt\.js|pubads/, r => r.abort())`
- If the homepage renders a degraded static fallback (no `#input-search`), just reload and retry up to ~8 times; it eventually loads the full SvelteKit app.
- `curl` gets 403 on `www.gamepix.com` and its `/_app/*` chunks — use the browser for anything there. `partners.gamepix.com` and `my.gamepix.com` are friendlier.

### Dashboard architecture (verified)
- Login page: `https://my.gamepix.com/login` — Angular/Ionic form `#login-form`, fields `ion-input[name="email"] input.native-input` and `ion-input[name="password"] input.native-input`, submit button `#sign-in` (disabled until Angular detects input — use `pressSequentially` for REAL keystrokes, then click).
- Game list: `https://my.gamepix.com/games` — game cards are `.data-item` with `p.title[title="<namespace>"]`.
- Game edit: `https://my.gamepix.com/games/<namespace>` — Ionic segment tabs: `ion-segment-button[value="info" | "assets" | "editorial" | "build" | "review"]` (label "Submit for Review" = value `review`).
- APIs: `api.gamepix.com/v3/devs/*` (drafts, uploads) and `api.partner.gamepix.com` (categories/companies). Watch responses with `page.on('response')` to confirm success (`PUT .../game-drafts/<ns>` 200 = saved; `PUT .../game-drafts/<ns>/review` 200 = submitted; `POST .../upload/asset` 200 = asset OK; S3 PUT = build upload OK).

### Ionic form interactions (critical)
- **Always use real Playwright clicks** (`locator.click()`) — they pierce shadow DOM and are trusted events. `el.click()` via `page.evaluate` silently fails on Ionic/Angular components (state doesn't update, Save stays disabled).
- Text inputs: click the native `input` inside `ion-input`/`ion-textarea` and `pressSequentially`.
- `ion-select` opens an `ion-popover`; pick an option with a real click on `ion-popover ion-item` (hasText), then the popover closes itself.
- `ionic-selectable` (tag pickers): real click on `.ionic-selectable` opens a modal list of `.ionic-selectable-item`s; real click on the desired item closes it. There are TWO pickers on the Info tab: **first = Main tag (single), second = Extra/secondary tags (multi-select)** — the second one stays open after clicking items; confirm with the modal footer button (OK/Save/Done). `publish.js` automates this via `GPX_EXTRA_TAGS` (comma-separated).
- `ion-checkbox`: real click toggles (verify via `classList.contains('checkbox-checked')`).
- An `ion-loading` overlay can block clicks: `await page.locator('ion-loading').first().waitFor({ state: 'hidden', timeout: 10000 })` before clicking tabs.
- A cookie/payment modal may appear on the games page: click `OK` / `Accept all` if visible before anything else.

## 2. Integrate the SDK (mandatory first script)

Insert as the FIRST script in `<head>` of `index.html`:

```html
<head>
  <script src="https://integration.gamepix.com/sdk/v3/gamepix.sdk.js"></script>
  ...
</head>
```

### 2a. Games coming from Poki or GameSnacks: install the AD BRIDGE (ads are the priority!)

Most portal games call their platform SDK for ads (`PokiSDK.commercialBreak()`, `GameSnacks.ad.break(...)`) and lifecycle events. If you strip that SDK, ads silently stop working. **Never ship a no-op driver** — use the validated bridges in `scripts/bridges/` (load as the FIRST script, before any game script):

| Game calls | Bridge | Real GamePix call |
|---|---|---|
| `PokiSDK.commercialBreak()` | `bridges/poki_bridge.js` | `GamePix.interstitialAd()` |
| `PokiSDK.rewardedBreak()` | `bridges/poki_bridge.js` | `GamePix.rewardAd()` |
| `PokiSDK.happyTime()` | `bridges/poki_bridge.js` | `GamePix.happyMoment()` |
| `PokiSDK.gameLoadingProgress/Finished()` | `bridges/poki_bridge.js` | `GamePix.loading()/loaded()` |
| `GameSnacks.ad.break({type:"next"})` | `bridges/snacks_bridge.js` | `GamePix.interstitialAd()` |
| `GameSnacks.ad.break({type:"reward"})` | `bridges/snacks_bridge.js` | `GamePix.rewardAd()` (full beforeAd→beforeReward→showAdFn→adViewed→afterAd→adBreakDone flow) |
| `GameSnacks.score.update(n)` / `game.levelComplete(n)` | `bridges/snacks_bridge.js` | `GamePix.updateScore(n)` / `updateLevel(n)+happyMoment()+interstitial` |
| `GameSnacks.game.ready()` | `bridges/snacks_bridge.js` | `GamePix.loaded()` |

Both bridges keep the original game surface intact (the game never knows), and each call is protected by a hard timeout so gameplay can never freeze if an ad fails.

### Audio-safe ad breaks (QA hard rule — added after a real rejection)

QA rejected a game because **"the game's music or sounds are audible while ads are playing"**. Never rely on the game pausing its own audio. Both bridges now hold audio themselves: they wrap the `AudioContext` constructor (loaded FIRST in `<head>`, before any game code) and track playing `<audio>`/`<video>` elements, then `suspend`/`pause` everything for the WHOLE ad break (interstitial AND rewarded) and restore exactly what was active in the completion callback.

For games WITHOUT a bridge (all other engines), implement the same behaviour yourself in your ad flow:

```js
// example for your own ad flow
var audioCtxs = [];                         // collect as you create them
function pauseAudio() {
  audioCtxs.forEach(function (c) { if (c.state === 'running') { c.__was = true; c.suspend(); } });
  document.querySelectorAll('audio, video').forEach(function (el) {
    if (!el.paused) { el.__was = true; el.pause(); }
  });
}
function resumeAudio() {
  audioCtxs.forEach(function (c) { if (c.__was) { c.__was = false; c.resume(); } });
  document.querySelectorAll('audio, video').forEach(function (el) { if (el.__was) { el.__was = false; el.play(); } });
}
// call pauseAudio() BEFORE the ad call, resumeAudio() in the .then()/afterAd callback
```

Also keep pausing on `visibilitychange` (checklist §3).

### Rewarded ads MUST be wired to a real bonus button (QA hard rule)

QA rejected a game because **"After clicking on the bonus button, no ad is displayed"**. Before submitting, verify every reward surface in the game:

1. Find every button/screen that promises a reward ("bonus", "watch ad for extra life/coins", "double score"...). For each one, the click path MUST call `GamePix.rewardAd()` and honour `res.success` (grant the reward only when true; let the user skip).
2. If the game is a Poki/GameSnacks port, the game must actually CALL `rewardedBreak()` / `ad.break({type:"reward"})` on those buttons — the bridges translate the calls, but a button that never calls is still a dead button.
3. `GamePix.loaded()` must have been called before any ad call (`GAMEPIX_LOADED_NOT_CALLED`).
4. A silent timeout failure is NOT a reward: play-test the flow; if no ad/reward happens, fix it before submitting (§8 Pre-QA gate).

**Do this BEFORE the first submission** — while a game is in review the build is LOCKED (see §7).

API (all optional except noted; errors are logged, never throw):

| Method | When to call |
|---|---|
| `GamePix.loading(pct)` | during load, number 0–100 |
| `GamePix.loaded()` | ONCE, when game is playable — MUST precede any other SDK call |
| `GamePix.localStorage.setItem/getItem/removeItem(key[, val])` | instead of native localStorage (string keys/values) |
| `GamePix.interstitialAd().then(res => {...})` | between levels — PAUSE game+audio before, RESUME in callback; call freely, fill is decided server-side |
| `GamePix.rewardAd().then(res => res.success)` | optional rewards; disclose the reward first; user must be able to skip |
| `GamePix.updateScore(n)` | every score change (positive integer) |
| `GamePix.updateLevel(n)` | every level change (positive integer) |
| `GamePix.lang()` | returns 2-letter code (`en`,`fr`,`it`,`de`,`es`,...) — default to English |
| `GamePix.happyMoment()` | unlocks/achievements/cool events |

Anti-scroll snippet (recommended):
```js
window.addEventListener("keydown", e => { if (["ArrowUp","ArrowDown"," "].includes(e.key)) e.preventDefault(); });
window.addEventListener("wheel", e => e.preventDefault(), { passive: false });
```

SDK error strings: `GAMEPIX_LOADED_NOT_CALLED`, `LOADED_ALREADY_CALLED`, `UPDATE_SCORE_VALUE_IS_NOT_A_NUMBER`, `UPDATE_LEVEL_VALUE_IS_NOT_A_NUMBER`, `LOADING_VALUE_IS_NOT_A_NUMBER`, `INTERSTITIAL_AD_CALLED_TWICE`, `REWARD_AD_CALLED_TWICE`, `KEY_OR_VALUE_FOR_LOCALSTORAGE_NOT_A_STRING`, `GAMEPIX_PLAYER_NOT_FOUND`.

## 2b. Neutralize sitelocks & external analytics (MANDATORY)

Portal games frequently ship sitelocks and telemetry that break on a new host.
Before packaging, the AI must:

- **Sitelocks / domain checks**: grep the whole build for `location.host`,
  `location.hostname`, `document.domain` and whitelist arrays; neutralize them
  (patch bytes with identical-length replacements, or short-circuit from the
  driver). A game that refuses to run on the target domain is a FAILED build.
- **External analytics / telemetry**: remove or redirect Sentry, Google
  Analytics, DDNA, Google Fonts, A/B test fetches, social-share `img` beacons
  and any third-party `<script>`/`<link>`. Only local files may remain.
  Verify with the firewall test (Phase 3 of the offliner pipeline) that zero
  non-localhost requests are made.
- **Service worker**: Construct/Cocos/Unity exports register a service worker
  whose cached responses hide real bodies. Block or remove the SW registration
  so capture and Playwright see the real files (`serviceWorkers: 'block'`).
- **Check**: `grep -rniE "location\.host|sentry|google-analytics|googletagmanager|fonts\.googleapis" <build>` must return 0 hits (aside from the SDK you intentionally integrate).

## 3. Compliance checklist (review will fail otherwise)

- ZIP zipped at FILE level: `index.html` at archive root (not inside a folder). Accepts `.zip` (or `.gpx` from the Unity plugin).
- All resources relative paths; no external links/analytics/ads/third-party SDKs; no `alert`/`confirm`; no "Rotate device" prompt; no quit button; no religious/explicit content.
- Pause game AND audio during ads and on `visibilitychange` (the bridges do this automatically — see §2a; else implement pauseAudio/resumeAudio yourself); every rewarded surface (bonus button) must actually call `GamePix.rewardAd()` and honour `res.success`; iframe-friendly at 640×480; `<meta name="viewport" content="...user-scalable=NO...">`; `touch-action:none` on body.
- Assets: icon **256×256** (≤1MB), cover **1360×850** (≤1.5MB), JPG/PNG. Per the GamePix submission guidelines the **game title must appear on the assets and must match the game's title** (rendered by the pipeline via `--title`); no other text, logos or branding. They MUST be **REAL GAMEPLAY captures of the build being submitted** (`scripts/capture_assets.js --title "<GPX_TITLE>"`): full-bleed square corners (rounded corners are a QA auto-reject), gameplay frames only (never a menu/loading screen), no HUD text in the crop.
- **NEVER submit assets that are not proven gameplay captures** (see §5b — publish.js enforces this: AI-generated and PIL-fallback assets are blocked at Submit).
- Description 100–500 chars, unique, no AI boilerplate; how-to-play ≤500 chars (controls first, Desktop/Mobile sections).
- Keep the build small. One ad at a time, between levels only, never timer-based.

### Game naming rule (IMPORTANT — read before publishing)
The GitHub repo name / project folder name is just an **internal label** —
the games shipped by this workflow carry **NO name inside the build** (no
hard-coded title in the code). Therefore:
- **NEVER** blindly reuse the GitHub repo/folder name, the original zip's
  filename, or any name found inside the game, as the game title.
- **Zip filename must match the submitted name.** If the build arrives as
  `santarun.zip` (or any other name), the agent **must rename/align it** so
  the submitted zip's name matches the chosen title (e.g. `santa-run.zip` /
  `santa-run-v1.0.0.zip`). A mismatched zip name is not acceptable at submit.
- **If the user provides no game name** (even if the original zip or the game
  itself had one), the agent **must invent a fresh, catchy, on-topic title**
  itself — never inherit the original name as-is. Read the code and play the
  game first, then pick a title that reflects what the game actually does.
- At publish time, the agent **chooses the game name itself**, right before
  going live: a fresh, catchy, **on-topic** title that reflects what the game
  actually does (read the code, play the game first). Not off-topic, not
  generic ("My Game", "Untitled"), not a clone of a famous title.
- The chosen title becomes `GPX_TITLE` (dashboard name + namespace slug) and
  the zip filename must match it. Assets carry the title via `--title` (§5) —
  the title lives in the dashboard metadata AND on the assets.

### Build cleaning rule (MANDATORY — 0 in-game hits)
The build shipped to GamePix must be **fully cleaned of its origin**: neither
the source platform name (whatever it is: Poki, Softgames, Construct, Phaser,
PixiJS, Unity, Godot, Famobi, GameSnacks, …) nor the original game name may
appear anywhere inside the shipped build — code, assets, atlases, manifests,
metadata, strings, URLs, file names. A partial clean is a FAILED build.

- **Repo/folder name is NOT the game title** (see above) and is also NOT proof
  of cleanliness — the build must be scrubbed regardless of the folder name.
- The original game name is **not** the repo name or the Pages URL for this
  workflow: the build simply carries **no name at all** inside it; the title
  is chosen fresh at publish time and lives only in the GamePix dashboard,
  the zip filename and the assets.
- Verify before submit with the provided test:
  `node scripts/verify_clean.js <build-dir> <platform-name> <original-game-name>`
  (add any extra terms as further args). It exits 0 only when every term has
  0 hits in both file contents and file names; otherwise it prints the
  offending files/lines and exits 1. Run it in CI/pre-submit; a non-zero exit
  blocks submission.

## 4. Automated publishing via Playwright

Full working implementation: `scripts/publish.js` (this repo). Usage:

```bash
GPX_EMAIL=you@example.com GPX_PASSWORD='secret' GPX_TITLE="My Game" \
GPX_MAIN_TAG="puzzle" GPX_EXTRA_TAGS="driving,simulation" GPX_DESCRIPTION="100-500 char original description..." \
GPX_HOW_TO_PLAY="Desktop\n... Mobile\n..." GPX_ICON=assets/icon_256.png GPX_COVER=assets/cover_1360x850.png \
GPX_ZIP=game-v1.0.0.zip GPX_RELEASE_NOTES="First release." \
xvfb-run -a node scripts/publish.js
```

The script performs, in order (each step verified by an API 200 or DOM state):
1. Login (re-login if redirected to /login; saves storageState to reuse the session).
2. Games page → dismiss payment modal if present.
3. **Create New Game** modal: title, main tag (ionic-selectable → item matching the tag name), description (100–500 chars) → `Create` → game appears in list (namespace = slugified title).
4. Open `games/<ns>`; **Info tab**: fix main tag if needed, **ALSO pick the extra/secondary tags** (2nd `.ionic-selectable`, multi-select — REQUIRED by this workflow, e.g. 2-4 tags matching the genre; other optional fields can stay empty), Orientation (`ion-select[name=orientation]` → Landscape), Desktop/Mobile friendly checkboxes, Game engine (`ion-select[name=gameEngine]` → Cocos/HTML5-JS/...), SDK integration checkbox → `Save` → wait for "Successfully updated!" toast and the warning "Please set Game Orientation" to disappear.
5. **Assets tab**: real-click the two `Add` buttons + `waitForEvent('filechooser')` → setFiles(icon/cover) → expect `POST .../upload/asset` 200 twice; tab text becomes "Assets: icon, cover" with "Modify" buttons.
6. **Editorial tab**: fill `textarea[name=howToPlay]` (≤500 chars) → click enabled `Save` (PUT 200).
7. **Build tab**: click `Browse File` + filechooser → setFiles(zip) → click enabled `Upload` → S3 PUT 200 → poll until "Build ready, your new version code is: <code>" (processing may take minutes; reload the tab while polling).
8. **Review tab**: fill the visible textarea (release notes) → click `Submit for review` → expect `PUT .../game-drafts/<ns>/review` 200 and the page badge to switch from EDIT to **REVIEW**.
9. Verify final state: `page.on('response')` capture of `GET api.gamepix.com/v3/devs/game-drafts/ns/<ns>` should show `"status":"review"`, `"buildStatus":"ready"`, `"buildUploaded":true`, `"buildMessage":"The build is reviewable"`.

### Gotchas that will bite you
- After clicking Create, the game list count increments (e.g. "4 Current Total") — use that + the card with `p[title="<ns>"]` to confirm.
- The Info `Save` is `ion-button` **disabled** until every required field is valid; if your synthetic state didn't register, re-do the selects with REAL clicks.
- `has-text:` filters match substrings; prefer exact regex `/^Save$/i` and check `hasAttribute('disabled')` before clicking.
- Save buttons exist per-tab (Info/Editorial); always click the last visible enabled one, or scope by the tab container.
- Processing a ~8MB zip takes 1–3 min. Poll, don't panic. Status strings: "Your build is in processing..." → "Build ready, your new version code is: X" → review.
- If a session expires mid-flow, the page redirects to `/login`; re-login (credentials typed with pressSequentially) then `goto` the target URL again.
- Never `page.evaluate(window.open=...)`-patch or JS-click Ionic components; trusted clicks only.

## 5. Assets = REAL GAMEPLAY CAPTURES of the build (mandatory, never AI-drawn)

GamePix QA **rejected 3 submitted games because their AI-generated assets "did not
correspond to the game tested"**, and 1 more because **"the game's icon has rounded
corners, which is not admitted"**. Assets derived from a text description are an
interpretation — QA compares them with the running game and rejects mismatches.
This failure mode is now IMPOSSIBLE by construction: the assets ARE the game.

### 5a. Primary pipeline — capture_assets.js (always use this)

Launch the ACTUAL build being submitted, get into real gameplay (NOT the menu!;
see the §5a bullet list), capture a burst of gameplay frames, and post-process them into compliant
assets:

```bash
npm i playwright && npx playwright install chromium   # run under xvfb on Linux
pip install pillow

xvfb-run -a node scripts/capture_assets.js \
  --zip build.zip --out-dir ./assets --settle 8 --frames 24 --title "My Game"
# -> assets/icon_256.png (256x256, <=1MB) + assets/cover_1360x850.png (1360x850, <=1.5MB)
# -> ASSETS_SOURCE=assets_gameplay_capture (the ONLY provenance publish.js accepts)
```

What the pipeline enforces (all rules came from real QA rejections):
- **Gameplay, not menus**: the game is auto-started (Play/Start/Tap-to-play overlays,
  language menus), then given a settle window (`--settle`, default 6 s) of simulated
  input so menus/load screens end up behind us; flat/detail-poor frames (menus,
  loading) and HUD-text-dominated frames are dropped by `capture_postprocess.py`.
- **Full-bleed square corners**: hard center cover-crops with 90° corners; a pixel
  probe verifies that all four icon corners are opaque and pixel-continuous —
  rounded-corner icons are re-cropped or rejected.
- **Exact specs**: icon 256×256 (≤1 MB), cover 1360×850 (≤1.5 MB), LANCZOS resize,
  JPEG fallback if a PNG exceeds the byte cap.
- **Icon readability**: the icon frame is chosen by scoring the CENTER THIRD of
  each frame, so the hero subject is big and readable at 256 px.
- `--dir <unpacked-dir>` may replace `--zip`; tune `--frames`/`--interval` if the
  game has slow animations, and `--width/--height` for portrait games.

### 5b. HARD RULE — only gameplay-capture assets may be submitted (enforced)

`publish.js` refuses to click **Submit for review** (exit code 2) unless the
`ASSETS_SOURCE` marker next to the icon/cover equals `assets_gameplay_capture`:

| ASSETS_SOURCE | Meaning | Submit? |
|---|---|---|
| `assets_gameplay_capture` | screenshots of THIS very build (capture_assets.js) | ✅ allowed |
| `assets_ai_generated` | FLUX/text-derived art — QA rejected it 3× ("not corresponding to the game tested") | 🚫 blocked |
| `assets_pil_fallback` | PIL-drawn placeholders (make_assets.py) | 🚫 blocked |
| missing / unknown | provenance unproven | 🚫 blocked |

Everything else (create, Info, Assets upload, Editorial, Build) stays idempotent and
is completed even when blocked; only the final submit is refused. Re-run `publish.js`
after regenerating acceptable assets — it will upload the new ones and submit.

Emergency fallback (only when NO build exists to capture): `scripts/gen_assets.py`
generates FLUX assets with full-bleed prompts and a square-corner pixel check — but
publish.js will refuse to submit them. Fix a rejected game only after QA returns it
to EDIT (§7), or use gen_assets output strictly for draft completion while a real
capture is being produced.

## 6. What "done" looks like

- Games list shows the game with status badge **REVIEW** (was **EDIT**).
- Draft API: `status:"review"`, `buildStatus:"ready"`, `buildMessage:"The build is reviewable"`.
- Notify the user: game submitted, QA typically takes a few days; edits are locked while in review (status switches to EDIT again if changes are requested).

## 7. REVIEW LOCK — build updates are impossible while in review (server-enforced)

Validated exhaustively (Sept 2026). Once a game is submitted for review (`status:"review"`):

- The Build tab hides Browse and disables Upload — **client-side only**. Bypassing the DOM does NOT help:
  - `GET /v3/devs/game-drafts/build-signed-url/<ns>?fileType=zip` → **401 `"Unauthorized game state."`** (server refuses while in review; the SAME call returns 200 for games never submitted).
- `PUT /v3/devs/game-drafts/<ns>` with `{status:"edit"}` → **400** `must NOT have additional properties: status` (status is not editable).
- There is **no withdraw/cancel-review/delete** feature anywhere (UI, bundles, API).
- Release notes textarea becomes `readonly`; asset inputs are also locked in review.
- The build download endpoint requires AWS SigV4 signing you cannot produce (`403 Authorization header requires 'Credential' parameter...`).

**Consequences — how to plan correctly:**
1. Finish EVERYTHING (SDK + ads bridge + score/level events + compliant build) BEFORE clicking Submit for review. The first submitted build should be the one you want QA to see.
2. If the QA requests changes, the game returns to EDIT and builds unlock again — that is the supported way to update.
3. Do not submit placeholder builds "just to test" — you burn the review slot with a build you can no longer replace.
4. Auth for direct API calls: the dashboard sends a **JWT `Authorization: Bearer ey...` header** (capture it via `page.on('request')`); cookies alone give 401.

## 8. PRE-QA VERIFICATION GATE — publish.js checks the RESULT, not just the steps

Added after **7 straight QA rejections**. Every rejection below is now a hard,
automated gate in `publish.js` (all fail-closed: everything except Submit is
completed, then the script exits with code 2 and prints the fix):

| QA rejection (7 games) | Gate now enforced in publish.js |
|---|---|
| Bonus button → no ad displayed; "add the Rewarded ad tag" | Wired-reward rule (§2a). The agent MUST play-test the reward flow and confirm a real `rewardAd()` click-path before submitting (manual gate; see the play-test checklist). |
| Assets "not corresponding to the game tested" (×3) | `ASSETS_SOURCE` must equal `assets_gameplay_capture` (§5b) — screenshots from `capture_assets.js` of THIS build. AI/PIL/unknown provenance → Submit blocked. |
| Icon has rounded corners | Full-bleed crops + 4-corner pixel probe in `capture_postprocess.py` (and `gen_assets.py` fallback regenerates on rounded corners). |
| Music/sounds audible during ads | Bridges hold audio automatically (§2a audio-safe shim). For non-bridged engines the SKILL integration step includes the pauseAudio/resumeAudio pattern — implemented or game not integrated. |
| "SDK Integration flagged but the SDK is not integrated" | The `sdkIntegration` checkbox is only ticked if `verifySdkInZip()` proves `gamepix.sdk.js` v3 is the FIRST script in `<head>` of the zipped `index.html`; if it fails, the box is un-flagged AND Submit is blocked with the fix path. |

Agent pre-QA checklist (run through this before any Submit):
1. Play the builder captures: confirm capture_assets.js reached real gameplay (`--settle` log, frames kept, not menus).
2. Click every reward surface in the running build; an ad must actually show (or `res.success` observable). Dead reward button → integrate/fix first.
3. No audio during ad breaks: trigger an ad, verify silence, verify gameplay is paused.
4. Icon/cover visually match what you just played (they are captures, so this should be trivially true).

## References
- SDK doc: https://partners.gamepix.com/sdk/doc/javascript (per-engine docs: /cocos, /construct2, /construct3, /gdevelop-5, /godot-plugin, /unity-plugin)
- Submission guidelines: https://partners.gamepix.com/guidelines/submission
- Dashboard: https://my.gamepix.com (login: /login)
