/*
 * game-driver.js — GamePix bridge for Construct 3 games expecting a PokiSDK surface.
 * Maps the game's ad/lifecycle calls onto the GamePix SDK so ads actually serve:
 *   PokiSDK.commercialBreak() -> GamePix.interstitialAd()
 *   PokiSDK.rewardedBreak()   -> GamePix.rewardAd()
 *   PokiSDK.gameLoadingProgress/Finished -> GamePix.loading()/loaded()
 *   PokiSDK.happyTime()       -> GamePix.happyMoment()
 * Loaded FIRST in <head>, before any game script.
 */
(function () {
  'use strict';
  if (typeof window === 'undefined') return;
  if (window.PokiSDK && window.PokiSDK.__gpxBridge) return;

  /* ---------- AUDIO-SAFE AD SHIM ------------------------------------------
   * QA hard rule: NO game audio may be audible while an ad (interstitial OR
   * rewarded) plays. The game itself cannot be trusted to pause its audio,
   * so the bridge holds it: every AudioContext (tracked by wrapping the
   * constructor at load time, BEFORE any game code runs) and every currently
   * playing <audio>/<video> element is suspended/paused for the whole break,
   * then exactly what was active is restored afterwards.
   */
  var audioShim = (function () {
    var ctxs = [];        // live AudioContexts
    var pausedMedia = []; // [{el}] media elements paused for the ad
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
    } catch (e) { /* tracking unavailable: nothing extra to suspend */ }
    return {
      suspendAll: function () {
        for (var i = 0; i < ctxs.length; i++) {
          try {
            var c = ctxs[i];
            if (c && typeof c.suspend === 'function' && c.state === 'running') { c.__gpxWasRunning = true; c.suspend(); }
          } catch (e) {}
        }
        var els = [];
        try { els = document.querySelectorAll('audio, video'); } catch (e) {}
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
            if (c && c.__gpxWasRunning && typeof c.resume === 'function') { c.__gpxWasRunning = false; c.resume(); }
          } catch (e) {}
        }
        for (var m = 0; m < pausedMedia.length; m++) { try { pausedMedia[m].el.play(); } catch (e) {} }
        pausedMedia = [];
      }
    };
  })();

  function gpx() { return (typeof window.GamePix === 'object' && window.GamePix) || null; }
  function markLoaded() {
    var g = gpx();
    if (!g) return;
    try {
      if (!window.GamePix._loadedDone) {
        window.GamePix._loadedDone = 1;
        if (typeof g.loading === 'function') { try { g.loading(100); } catch (e) {} }
        if (typeof g.loaded === 'function') g.loaded();
      }
    } catch (e) {}
  }
  function setProgress(p) {
    var g = gpx(); if (!g || typeof g.loading !== 'function') return;
    var v = Number(p); if (!isFinite(v)) return;
    v = Math.max(0, Math.min(99, Math.floor(v)));
    try { g.loading(v); } catch (e) {}
  }

  var bridge = {
    __gpxBridge: true,

    init: function () { return Promise.resolve(); },
    setDebug: function () {},
    setLogCloudflare: function () {},
    debug: function () {},

    gameLoadingStart: function () { setProgress(1); },
    gameLoadingProgress: function (p) { setProgress(p); },
    gameLoadingFinished: function () { markLoaded(); },

    gameplayStart: function () {},
    gameplayStop: function () {},

    /* Interstitial: the bridge itself holds the audio for the whole break. */
    commercialBreak: function () {
      audioShim.suspendAll();
      return new Promise(function (resolve) {
        var done = false;
        var fin = function () { if (!done) { done = true; audioShim.resumeAll(); resolve(); } };
        var g = gpx();
        try {
          if (g && typeof g.interstitialAd === 'function') {
            g.interstitialAd().then(fin, fin);
          } else fin();
        } catch (e) { fin(); }
        setTimeout(fin, 25000); // never freeze gameplay if the promise hangs
      });
    },

    /* Rewarded: resolve({success:true}) only when the ad was fully watched.
       Audio is held by the bridge for the whole break (QA hard rule). */
    rewardedBreak: function () {
      audioShim.suspendAll();
      return new Promise(function (resolve) {
        var done = false;
        var fin = function (ok) { if (!done) { done = true; audioShim.resumeAll(); resolve({ success: !!ok }); } };
        var g = gpx();
        try {
          if (g && typeof g.rewardAd === 'function') {
            g.rewardAd().then(function (res) { fin(!!(res && res.success)); }, function () { fin(false); });
          } else fin(false);
        } catch (e) { fin(false); }
        setTimeout(function () { fin(false); }, 60000);
      });
    },

    happyTime: function () {
      var g = gpx();
      try { if (g && typeof g.happyMoment === 'function') g.happyMoment(); } catch (e) {}
    },

    customEvent: function () {},
    logEvent: function () {},
    shareableURL: function () { return Promise.resolve({ url: location.href }); },
    sendClientEvent: function () {},
  };

  window.PokiSDK = bridge;
  window.PokiHasInitialised = true;
  if (!window.PokiSDK_InitOK) window.PokiSDK_InitOK = true;

  window.addEventListener('unhandledrejection', function (e) {
    if (e && e.reason && /poki|gamepix/i.test(String((e.reason && e.reason.message) || ''))) e.preventDefault();
  });
})();
