// Top frame only: decides whether music is playing and makes the sprite dance.
(() => {
  const SPRITE = { url: chrome.runtime.getURL('images/sprite.png'), width: 128, height: 172 };
  const SCALES = { small: 0.75, medium: 1, large: 1.5 };
  const FRAME_TIMEOUT = 4000; // ms; a frame that stops reporting has gone away
  const LINGER = 2500; // ms he stays after the music stops
  const AUDIBLE_LAG = 3000; // ms Chrome's audible flag can trail the real state
  const FREESTYLE_GAP = 500; // ms between beats when the audio cannot be heard (120 BPM)
  const NOTE = ['...##..', '...###.', '...#.##', '...#..#', '...#...', '...#...', '.###...', '####...', '####...', '.##....'];

  const CSS = `
    .stage { position: absolute; inset: 0; opacity: 0; transform: translateY(40%);
      transition: transform .4s cubic-bezier(.3, 1.5, .5, 1), opacity .2s; }
    .stage.on { opacity: 1; transform: none; }
    .sponge { display: block; width: 100%; height: 100%; transform-origin: 50% 100%; will-change: transform;
      pointer-events: none; cursor: grab; user-select: none; -webkit-user-drag: none; touch-action: none; }
    .stage.on .sponge { pointer-events: auto; }
    .sponge.pixel { image-rendering: pixelated; }
    .sponge.flip { scale: -1 1; }
    .sponge:active { cursor: grabbing; }
    .note { position: absolute; top: 6%; width: 2px; height: 2px; color: #e8915a;
      animation: float 1.1s ease-out forwards; }
    @keyframes float {
      from { transform: translate(0, 0); opacity: 1; }
      to { transform: translate(var(--drift), -70px); opacity: 0; }
    }
    @media print { .stage { display: none; } }
  `;

  const frames = new Map(); // frameId -> { audio, signal, at }
  // `enabled` is dancing to music; `always` keeps him on the page whether or not anything plays.
  const settings = { enabled: true, always: false, flip: false, size: 'medium', pos: { right: 24, bottom: 8 } };
  let audible = false; // Chrome's "this tab is making sound" flag
  let lastReport = -Infinity; // when a frame last reported on its own media
  let recheck = 0;
  let capture = null; // { signal } while the whole tab is being listened to
  let ui = null;
  let visible = false;
  let raf = 0;
  let quietSince = 0;
  let lastFrame = 0;
  let lastBeat = 0;
  let beats = 0;
  let bpm = 0;
  let side = 1;
  const bounce = { x: 0, v: 0 }; // below zero is squashed, above is stretched
  const lean = { x: 0, v: 0, target: 0 }; // degrees

  // 'synced': hearing the audio. 'freestyle': something is playing but cannot be heard.
  function mode() {
    if (!settings.enabled) return 'off';
    if (capture) return capture.signal ? 'synced' : 'quiet';
    const now = performance.now();
    let synced = false;
    let blocked = false;
    for (const [id, frame] of frames) {
      if (now - frame.at > FRAME_TIMEOUT) frames.delete(id);
      else if (frame.audio === 'synced') synced ||= frame.signal;
      else if (frame.audio === 'blocked') blocked = true;
    }
    if (synced) return 'synced';
    // Chrome's flag lags a couple of seconds behind, so right after a frame reports silence it is stale.
    if (blocked || (audible && !frames.size && now - lastReport > AUDIBLE_LAG)) return 'freestyle';
    return 'quiet';
  }

  function update() {
    const current = mode();
    const dancing = current === 'synced' || current === 'freestyle';
    const now = performance.now();
    if (dancing) quietSince = 0;
    else if (!quietSince) quietSince = now;
    const show = settings.always || dancing || (visible && current === 'quiet' && now - quietSince < LINGER);
    if (show !== visible) setVisible(show);
    // Standing still in always-on mode needs no animation loop; everything else does.
    if (visible && (dancing || !settings.always)) wake();
    // Nothing else re-evaluates while he is hidden and the tab still counts as audible.
    if (audible && current === 'quiet' && !recheck) {
      recheck = setTimeout(() => {
        recheck = 0;
        update();
      }, 1000);
    }
    return current;
  }

  function build() {
    // XML and SVG documents cannot host the overlay.
    if (!(document.documentElement instanceof HTMLHtmlElement)) return false;
    // Let the parser add <head> and <body> first so he is never the first child of <html>.
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', update, { once: true });
      return false;
    }
    const host = document.createElement('spongebeat-dancer');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'all: initial; position: fixed; z-index: 2147483647; pointer-events: none;';
    const root = host.attachShadow({ mode: 'closed' });
    // Constructed sheet and DOM calls only: pages with a strict CSP or Trusted Types reject inline HTML.
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
    const stage = document.createElement('div');
    stage.className = 'stage';
    const img = document.createElement('img');
    img.className = 'sponge';
    img.alt = '';
    img.draggable = false;
    img.src = SPRITE.url;
    stage.append(img);
    root.append(stage);
    document.documentElement.append(host);

    let drag = null;
    img.addEventListener('pointerdown', (event) => {
      if (event.button) return;
      event.preventDefault();
      img.setPointerCapture(event.pointerId);
      drag = { x: event.clientX, y: event.clientY, ...settings.pos };
    });
    img.addEventListener('pointermove', (event) => {
      if (!drag) return;
      settings.pos = { right: drag.right - (event.clientX - drag.x), bottom: drag.bottom - (event.clientY - drag.y) };
      place();
    });
    const drop = () => {
      if (!drag) return;
      drag = null;
      try {
        chrome.storage.local.set({ pos: settings.pos });
      } catch {}
    };
    img.addEventListener('pointerup', drop);
    img.addEventListener('pointercancel', drop);
    window.addEventListener('resize', place);

    const shadow = [];
    NOTE.forEach((row, y) => [...row].forEach((cell, x) => cell === '#' && shadow.push(`${x * 2}px ${y * 2}px`)));
    ui = { host, stage, img, noteShadow: shadow.join(', ') };
    place();
    return true;
  }

  function place() {
    if (!ui) return;
    const scale = SCALES[settings.size] || 1;
    const width = SPRITE.width * scale;
    const height = SPRITE.height * scale;
    const clamp = (value, max) => Math.max(0, Math.min(value, Math.max(0, max)));
    settings.pos.right = clamp(settings.pos.right, window.innerWidth - width);
    settings.pos.bottom = clamp(settings.pos.bottom, window.innerHeight - height);
    const style = ui.host.style;
    style.width = `${width}px`;
    style.height = `${height}px`;
    style.right = `${settings.pos.right}px`;
    style.bottom = `${settings.pos.bottom}px`;
    // Shrinking with nearest-neighbour would drop the one-pixel face lines.
    ui.img.classList.toggle('pixel', scale >= 1);
    ui.img.classList.toggle('flip', settings.flip);
  }

  function wake() {
    if (raf) return;
    lastFrame = performance.now();
    raf = requestAnimationFrame(frame);
  }

  function rest() {
    cancelAnimationFrame(raf);
    raf = 0;
    bounce.x = bounce.v = lean.x = lean.v = lean.target = 0;
    if (ui) ui.img.style.transform = '';
  }

  function setVisible(show) {
    if (show && !ui && !build()) return;
    visible = show;
    if (show) {
      if (!ui.host.isConnected) document.documentElement.append(ui.host);
      // Let the hidden state render first so the pop-in transition runs.
      requestAnimationFrame(() => visible && ui.stage.classList.add('on'));
      wake();
    } else {
      ui?.stage.classList.remove('on');
      rest();
    }
  }

  function beat(strength) {
    if (!settings.enabled) return;
    lastBeat = performance.now();
    beats++;
    if (!visible) return;
    wake();
    side = -side;
    bounce.x = Math.min(bounce.x, -(0.1 + 0.12 * strength));
    bounce.v = 0;
    lean.target = side * (5 + 7 * strength);
    if (beats % 2 === 0) {
      const note = document.createElement('i');
      note.className = 'note';
      note.style.left = side > 0 ? '84%' : '6%';
      note.style.boxShadow = ui.noteShadow;
      note.style.setProperty('--drift', `${side * 26}px`);
      note.addEventListener('animationend', () => note.remove());
      ui.stage.append(note);
    }
  }

  function spring(state, target, stiffness, damping, dt) {
    state.v += (-(state.x - target) * stiffness - state.v * damping) * dt;
    state.x += state.v * dt;
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - lastFrame) / 1000);
    lastFrame = now;
    const current = update();
    if (!visible) return;
    if (current === 'freestyle' && now - lastBeat >= FREESTYLE_GAP) {
      bpm = 0;
      beat(0.6);
    }
    // Squash on the beat, then spring back past upright into a small hop.
    spring(bounce, 0, 170, 8, dt);
    lean.target *= Math.exp(-dt * 1.2);
    spring(lean, lean.target, 120, 12, dt);
    const settled = Math.abs(bounce.x) + Math.abs(bounce.v) < 1e-3 && Math.abs(lean.x) + Math.abs(lean.v) < 0.05;
    if (settings.always && current !== 'synced' && current !== 'freestyle' && settled) {
      rest(); // a beat or a change of state wakes the loop again
      return;
    }
    const hop = Math.max(0, bounce.x) * ui.host.offsetHeight * 0.6;
    ui.img.style.transform =
      `translate(${(lean.x * 0.8).toFixed(2)}px, ${(-hop).toFixed(2)}px) rotate(${lean.x.toFixed(2)}deg) ` +
      `scale(${(1 - bounce.x * 0.55).toFixed(3)}, ${(1 + bounce.x).toFixed(3)})`;
  }

  // Messages from this frame's listener (directly) and from other frames (via the background).
  function receive(message, frameId) {
    if (message.type === 'frame') {
      lastReport = performance.now();
      if (message.audio === 'none') frames.delete(frameId);
      else frames.set(frameId, { audio: message.audio, signal: message.signal, at: performance.now() });
      update();
    } else if (message.type === 'beat' && !capture) {
      bpm = message.bpm;
      update();
      beat(message.strength);
    }
  }
  globalThis.__spongeBeat = { receive };

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.relay) {
      receive(message.relay, message.frameId);
    } else if (message.type === 'audible') {
      audible = message.audible;
      update();
    } else if (message.type === 'capture') {
      capture = message.on ? { signal: false } : null;
      update();
    } else if (message.type === 'capture-signal' && capture) {
      capture.signal = message.signal;
      update();
    } else if (message.type === 'capture-beat' && capture) {
      bpm = message.bpm;
      beat(message.strength);
    } else if (message.type === 'status') {
      sendResponse({ mode: mode(), bpm, beats, visible, capture: !!capture });
    }
  });

  chrome.storage.local.get(settings).then((stored) => {
    Object.assign(settings, stored);
    place();
    update();
  });
  chrome.storage.onChanged.addListener((changes) => {
    for (const key of Object.keys(settings)) {
      if (changes[key] && changes[key].newValue !== undefined) settings[key] = changes[key].newValue;
    }
    place();
    update();
  });
  chrome.runtime.sendMessage({ type: 'hello' }).then((state) => {
    if (!state) return;
    audible = state.audible;
    capture = state.capture ? { signal: false } : null;
    update();
  }).catch(() => {});
})();
