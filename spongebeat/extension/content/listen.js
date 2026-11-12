// Runs in every frame: finds media elements that are audibly playing, taps
// their audio and reports beats to the dancer in the top frame.
(() => {
  const TOP = window.top === window;
  const IDLE_STOP = 3000; // ms without audible media before the loop stops

  const media = new Set();
  const taps = new WeakMap(); // element -> { state: 'heard' | 'silent' | 'blocked', stream, node }
  let enabled = true;
  let captureOn = false; // the whole tab is being listened to instead
  let ctx = null;
  let analyser = null;
  let detector = null;
  let timer = 0;
  let idleSince = 0;
  let lastResume = 0;
  let sentKey = '';
  let sentAt = 0;

  function send(message) {
    if (TOP) {
      globalThis.__spongeBeat?.receive(message, 0);
      return;
    }
    try {
      chrome.runtime.sendMessage({ relay: message }).catch(() => {});
    } catch {
      stop(); // the extension was reloaded and this script is orphaned
    }
  }

  const isAudible = (el) => !el.paused && !el.ended && !el.muted && el.volume > 0 && el.readyState > 2;

  // Chrome-only decoder counter; lets silent videos be ignored when the audio cannot be tapped.
  const hasAudio = (el) => el.webkitAudioDecodedByteCount === undefined || el.webkitAudioDecodedByteCount > 0;

  function graph() {
    if (ctx) return;
    ctx = new AudioContext();
    analyser = ctx.createAnalyser();
    detector = new BeatDetector(analyser);
  }

  function tapFor(el) {
    let tap = taps.get(el);
    if (tap) return tap;
    tap = { state: 'silent', stream: null, node: null, dead: false };
    taps.set(el, tap);

    const wire = () => {
      if (tap.dead) return;
      try {
        tap.node?.disconnect();
        tap.node = null;
        // Only the sound is needed; an unread video track still costs frame copies.
        for (const track of tap.stream.getVideoTracks()) track.stop();
        if (!tap.stream.getAudioTracks().length) {
          tap.state = 'silent';
          return;
        }
        graph();
        tap.node = ctx.createMediaStreamSource(tap.stream);
        tap.node.connect(analyser);
        tap.state = 'heard';
      } catch {
        tap.state = 'blocked';
      }
    };

    // A new source may have different capture rights, so start over when one loads.
    el.addEventListener('loadedmetadata', () => {
      tap.dead = true;
      tap.node?.disconnect();
      if (taps.get(el) === tap) taps.delete(el);
    }, { once: true });

    try {
      // Throws for cross-origin media without CORS and for DRM; playback is unaffected.
      tap.stream = el.captureStream();
      tap.stream.addEventListener('addtrack', wire);
      tap.stream.addEventListener('removetrack', wire);
      wire();
    } catch {
      tap.state = 'blocked';
    }
    return tap;
  }

  function tick() {
    if (document.hidden) return;
    const now = performance.now();
    let heard = 0;
    let blocked = 0;
    for (const el of media) {
      if (!isAudible(el)) {
        if (el.paused && !el.isConnected) media.delete(el);
        continue;
      }
      if (!enabled || captureOn) continue;
      const tap = tapFor(el);
      if (tap.state === 'heard') heard++;
      else if (tap.state === 'blocked' && hasAudio(el)) blocked++;
    }

    let audio = 'none';
    let signal = false;
    if (heard && ctx.state !== 'running') {
      // Autoplay policy can hold the context back until the page has been clicked.
      if (now - lastResume > 1000) {
        lastResume = now;
        ctx.resume().catch(() => {});
      }
      audio = 'blocked';
    } else if (heard) {
      const result = detector.tick(now / 1000);
      audio = 'synced';
      signal = result.signal;
      if (result.beat) send({ type: 'beat', strength: result.strength, bpm: result.bpm });
    } else if (blocked) {
      audio = 'blocked';
    }

    // Report changes at once and repeat as a heartbeat so a vanished frame times out.
    const key = audio + signal;
    if (key !== sentKey || (audio !== 'none' && now - sentAt > 1000)) {
      sentKey = key;
      sentAt = now;
      send({ type: 'frame', audio, signal });
    }

    if (audio !== 'none') idleSince = 0;
    else if (!idleSince) idleSince = now;
    else if (now - idleSince > IDLE_STOP) stop();
  }

  function start() {
    if (timer || !media.size) return;
    idleSince = 0;
    // A timer rather than requestAnimationFrame: off-screen iframes get no animation frames.
    timer = setInterval(tick, 16);
  }

  function stop() {
    clearInterval(timer);
    timer = 0;
    for (const el of media) if (el.paused) media.delete(el);
    if (ctx?.state === 'running') ctx.suspend().catch(() => {});
  }

  function onMedia(event) {
    const el = event.type === 'spongebeat:media' ? event.relatedTarget || event.composedPath()[0] : event.target;
    if (el instanceof HTMLMediaElement) {
      media.add(el);
      start();
    }
  }
  for (const type of ['play', 'playing', 'volumechange', 'spongebeat:media']) {
    document.addEventListener(type, onMedia, true);
  }

  chrome.storage.local.get({ enabled: true }).then((stored) => {
    enabled = stored.enabled;
  });
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.enabled) {
      enabled = changes.enabled.newValue !== false;
      start();
    }
  });
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === 'capture') {
      captureOn = message.on;
      start();
    }
  });
})();
