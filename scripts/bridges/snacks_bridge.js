/*
 * game-driver.js — GamePix bridge for games expecting the GameSnacks surface.
 * Maps the game's ad/lifecycle calls onto the GamePix SDK so ads actually serve:
 *   GameSnacks.ad.break({type:"next"})   -> GamePix.interstitialAd()
 *   GameSnacks.ad.break({type:"reward"}) -> GamePix.rewardAd() (full h5games callback flow)
 *   GameSnacks.game.ready()              -> GamePix.loaded()
 *   GameSnacks.game.levelComplete(n)     -> GamePix.updateLevel(n) + happyMoment + interstitial
 *   GameSnacks.game.gameOver()           -> interstitial moment
 *   GameSnacks.score.update(n)           -> GamePix.updateScore(n)
 * Loaded FIRST in <head>, before any game script.
 */
(function () {
  "use strict";
  if (typeof window === "undefined") return;
  if (window.GameSnacks && window.GameSnacks.__gpxBridge) return;

  /* ------- AUDIO-SAFE AD SHIM (identical to poki_bridge.js) -------
   * QA hard rule: NO game audio while an ad plays. The bridge must not rely
   * on the game pausing itself: it tracks every AudioContext (wrapped ctor,
   * loaded BEFORE any game code) and playing <audio>/<video> elements, and
   * holds them for the entire ad break (reward AND interstitial).
   */
  var audioShim = (function () {
    var ctxs = [];
    var pausedMedia = [];
    function track(ctx) { if (ctx && ctxs.indexOf(ctx) < 0) ctxs.push(ctx); }
    try {
      if (window.AudioContext) {
        var Base = window.AudioContext;
        function Tracked() {
          var c = new (Function.prototype.bind.apply(Base, [null].concat([].slice.call(arguments))))();
          track(c); return c;
        }
        Tracked.prototype = Base.prototype;
        window.AudioContext = Tracked;
      }
      if (window.webkitAudioContext) {
        var WBase = window.webkitAudioContext;
        function WTracked() {
          var c = new (Function.prototype.bind.apply(WBase, [null].concat([].slice.call(arguments))))();
          track(c); return c;
        }
        WTracked.prototype = WBase.prototype;
        window.webkitAudioContext = WTracked;
      }
    } catch (e) { /* fall through: nothing extra to suspend */ }
    return {
      suspendAll: function () {
        for (var i = 0; i < ctxs.length; i++) {
          try {
            var c = ctxs[i];
            if (c && typeof c.suspend === "function" && c.state === "running") { c.__gpxWasRunning = true; c.suspend(); }
          } catch (e) {}
        }
        var els = [];
        try { els = document.querySelectorAll("audio, video"); } catch (e) {}
        for (var j = 0; j < els.length; j++) {
          try {
            var el = els[j];
            if (el && !el.paused) { pausedMedia.push({ el: el }); el.pause(); }
          } catch (e) {}
        }
      },
      resumeAll: function () {
        for (var k = 0; k < ctxs.length; k++) {
          try {
            var c = ctxs[k];
            if (c && c.__gpxWasRunning && typeof c.resume === "function") { c.__gpxWasRunning = false; c.resume(); }
          } catch (e) {}
        }
        for (var m = 0; m < pausedMedia.length; m++) { try { pausedMedia[m].el.play(); } catch (e) {} }
        pausedMedia = [];
      }
    };
  })();

  var TAG = "[GPXBridge]";
  function log() { try { console.debug.apply(console, [TAG].concat([].slice.call(arguments))); } catch (e) {} }
  function fn(f) { return typeof f === "function" ? f : null; }
  function gpx() { return (typeof window.GamePix === "object" && window.GamePix) || null; }
  function num(v) { v = Number(v); return isFinite(v) && v >= 0 ? Math.floor(v) : null; }

  function markLoaded() {
    var g = gpx(); if (!g) return;
    try {
      if (!window.GamePix._loadedDone) {
        window.GamePix._loadedDone = 1;
        if (typeof g.loading === "function") { try { g.loading(100); } catch (e) {} }
        if (typeof g.loaded === "function") g.loaded();
        log("GamePix.loaded() sent");
      }
    } catch (e) {}
  }

  function showInterstitial() {
    var g = gpx();
    try { if (g && typeof g.interstitialAd === "function") { g.interstitialAd().catch(function () {}); } } catch (e) {}
  }

  var GameSnacks = {
    __gpxBridge: true,
    version: "gpx-bridge-1.0.0",

    game: {
      ready: function () { markLoaded(); },
      firstFrameReady: function () { /* first frame implies playable readiness here */ markLoaded(); },

      gameOver: function () { showInterstitial(); },

      levelComplete: function (n) {
        var g = gpx();
        var v = num(n);
        try { if (g && v !== null && typeof g.updateLevel === "function") g.updateLevel(v); } catch (e) {}
        try { if (g && typeof g.happyMoment === "function") g.happyMoment(); } catch (e) {}
        showInterstitial();
      },

      onPause: function (cb) {
        cb = fn(cb);
        document.addEventListener("visibilitychange", function () {
          if (document.hidden && cb) { log("onPause fire"); cb(); }
        });
      },
      onResume: function (cb) {
        cb = fn(cb);
        document.addEventListener("visibilitychange", function () {
          if (!document.hidden && cb) { log("onResume fire"); cb(); }
        });
      }
    },

    audio: {
      isEnabled: function () { return true; },
      subscribe: function (cb) {
        cb = fn(cb);
        if (cb) {
          var fire = function () { try { cb(true); } catch (e) { log("audio cb err", e); } };
          if (document.readyState === "complete") setTimeout(fire, 300);
          else window.addEventListener("load", function () { setTimeout(fire, 300); });
        }
      }
    },

    storage: {
      // Synchronous string semantics (game code relies on sync access).
      getItem: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
      setItem: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }
    },

    score: {
      update: function (n) {
        var g = gpx(); var v = num(n);
        try { if (g && v !== null && typeof g.updateScore === "function") g.updateScore(v); } catch (e) {}
      }
    },

    ad: {
      // h5games ad-break flow bridged onto GamePix ads.
      break: function (opts) {
        opts = opts || {};
        log("ad.break type=" + (opts.type || "next"));
        var done = false;
        var finish = function (status) {
          if (done) return; done = true;
          audioShim.resumeAll();
          var a = fn(opts.afterAd); if (a) { try { a(); } catch (e) {} }
          var d = fn(opts.adBreakDone);
          if (d) { try { d({ breakStatus: status, type: opts.type || "next" }); } catch (e) {} }
        };
        var finishViewed = function () { finish("viewed"); };
        var finishSkipped = function () { finish("dismissed"); };

        var beforeAd = fn(opts.beforeAd);
        if (beforeAd) { try { beforeAd(); } catch (e) {} }

        // The bridge, not the game, holds the audio for the whole break.
        audioShim.suspendAll();

        var g = gpx();
        try {
          if (opts.type === "reward" && g && typeof g.rewardAd === "function") {
            g.rewardAd().then(function (res) {
              if (res && res.success) {
                var proceed = function () {
                  var show = fn(opts.showAdFn); if (show) { try { show(); } catch (e) {} }
                  var v = fn(opts.adViewed); if (v) { try { v(); } catch (e) {} }
                  finishViewed();
                };
                var br = fn(opts.beforeReward);
                if (br) { try { br(proceed); } catch (e) { finishViewed(); } }
                else proceed();
              } else finishSkipped();
            }).catch(finishSkipped);
          } else if (g && typeof g.interstitialAd === "function") {
            g.interstitialAd().then(finishViewed, finishSkipped);
          } else finishViewed();
        } catch (e) { finishViewed(); }

        setTimeout(finishViewed, 60000); // hard safety: never block the game
      }
    }
  };

  if (typeof window.c2_callFunction !== "function") {
    window.c2_callFunction = function () {};
  }

  window.GameSnacks = GameSnacks;
  window.GameDriver = GameSnacks; // alias

  window.addEventListener("error", function (e) {
    log("window.onerror:", e.message, e.filename, e.lineno);
  });
  log("initialized");
})();
