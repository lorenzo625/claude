// Hidden page that listens to captured tab audio and reports beats per tab.
const captures = new Map(); // tabId -> { stream, ctx, detector, signal, sentAt }

async function start(tabId, streamId) {
  stop(tabId);
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
  });
  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  // A captured tab goes silent for the user unless its audio is played back here.
  source.connect(ctx.destination);
  const analyser = ctx.createAnalyser();
  source.connect(analyser);
  captures.set(tabId, { stream, ctx, detector: new BeatDetector(analyser), signal: null, sentAt: 0 });
  stream.getAudioTracks()[0].addEventListener('ended', () => {
    stop(tabId);
    chrome.runtime.sendMessage({ type: 'capture-ended', tabId });
  });
  await ctx.resume();
}

function stop(tabId) {
  const capture = captures.get(tabId);
  if (!capture) return;
  captures.delete(tabId);
  for (const track of capture.stream.getTracks()) track.stop();
  capture.ctx.close();
}

// Timers on a hidden page can be throttled; a worker's are not.
new Worker('ticker.js').onmessage = () => {
  const now = performance.now();
  for (const [tabId, capture] of captures) {
    const result = capture.detector.tick(now / 1000);
    if (result.beat) {
      chrome.runtime.sendMessage({ type: 'capture-beat', tabId, strength: result.strength, bpm: result.bpm });
    }
    // Repeat the state every second so a reloaded page catches up.
    if (result.signal !== capture.signal || now - capture.sentAt > 1000) {
      capture.signal = result.signal;
      capture.sentAt = now;
      chrome.runtime.sendMessage({ type: 'capture-signal', tabId, signal: result.signal });
    }
  }
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== 'offscreen') return;
  if (message.type === 'start') {
    start(message.tabId, message.streamId).then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error.message || error) }),
    );
    return true;
  }
  if (message.type === 'stop') {
    stop(message.tabId);
    sendResponse({ remaining: captures.size });
  }
});
