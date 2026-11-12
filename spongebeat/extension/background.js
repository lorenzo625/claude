// Relays messages between frames and runs the optional whole-tab listening.
const toTop = (tabId, message) => chrome.tabs.sendMessage(tabId, message, { frameId: 0 }).catch(() => {});
const toAllFrames = (tabId, message) => chrome.tabs.sendMessage(tabId, message).catch(() => {});

// Captured tab ids live in session storage because the service worker is short-lived.
async function capturedTabs() {
  return (await chrome.storage.session.get({ captures: [] })).captures;
}

async function hasOffscreen() {
  return (await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] })).length > 0;
}

async function startCapture(tabId, streamId) {
  if (!(await hasOffscreen())) {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['USER_MEDIA'],
      justification: 'Listens to the tab audio to find the beat.',
    });
  }
  const result = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'start', tabId, streamId });
  if (!result?.ok) {
    await stopCapture(tabId);
    return result || { ok: false, error: 'No answer from the listener.' };
  }
  const tabs = await capturedTabs();
  if (!tabs.includes(tabId)) await chrome.storage.session.set({ captures: [...tabs, tabId] });
  toAllFrames(tabId, { type: 'capture', on: true });
  return result;
}

async function stopCapture(tabId) {
  const tabs = await capturedTabs();
  if (tabs.includes(tabId)) await chrome.storage.session.set({ captures: tabs.filter((id) => id !== tabId) });
  toAllFrames(tabId, { type: 'capture', on: false });
  if (await hasOffscreen()) {
    const result = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'stop', tabId }).catch(() => null);
    if (!result || result.remaining === 0) await chrome.offscreen.closeDocument().catch(() => {});
  }
  return { ok: true };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.relay && sender.tab) {
    toTop(sender.tab.id, { relay: message.relay, frameId: sender.frameId });
  } else if (message.type === 'hello' && sender.tab) {
    capturedTabs().then((tabs) => sendResponse({ audible: !!sender.tab.audible, capture: tabs.includes(sender.tab.id) }));
    return true;
  } else if (message.type === 'start-capture') {
    startCapture(message.tabId, message.streamId).then(sendResponse, (error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  } else if (message.type === 'stop-capture') {
    stopCapture(message.tabId).then(sendResponse);
    return true;
  } else if (message.type === 'capture-beat' || message.type === 'capture-signal') {
    toTop(message.tabId, message);
  } else if (message.type === 'capture-ended') {
    stopCapture(message.tabId);
  }
});

chrome.tabs.onUpdated.addListener((tabId, change) => {
  if ('audible' in change) toTop(tabId, { type: 'audible', audible: change.audible });
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  if ((await capturedTabs()).includes(tabId)) stopCapture(tabId);
});
