const statusText = document.getElementById('status');
const switches = document.querySelectorAll('input[role="switch"]'); // each id is its settings key
const captureButton = document.getElementById('capture');
const hint = document.getElementById('hint');
let tab = null;
let capturing = false;

function describe(status) {
  if (!status) {
    return /^(https?|file):/.test(tab?.url || '')
      ? 'Reload this page to wake him up.'
      : "He can't dance on this page.";
  }
  switch (status.mode) {
    case 'off':
      return status.visible ? 'Standing still. Dancing is switched off.' : 'Switched off.';
    case 'synced':
      return status.bpm ? `Dancing to the beat · ${status.bpm} BPM` : 'Dancing to the beat.';
    case 'freestyle':
      return "Free-styling: this site won't share its audio. Use the button below to sync him to the real beat.";
    default:
      return status.capture ? 'Listening to this tab. Play something!' : 'Waiting for music.';
  }
}

async function refresh() {
  const status = tab ? await chrome.tabs.sendMessage(tab.id, { type: 'status' }, { frameId: 0 }).catch(() => null) : null;
  capturing = !!status?.capture;
  statusText.textContent = describe(status);
  captureButton.disabled = !status;
  captureButton.textContent = capturing ? 'Stop listening to this tab' : "Sync to this tab's audio";
  captureButton.setAttribute('aria-pressed', String(capturing));
}

function fail(error) {
  hint.textContent = `Couldn't listen to this tab: ${error}`;
  hint.classList.add('error');
}

captureButton.addEventListener('click', async () => {
  try {
    let result;
    if (capturing) {
      result = await chrome.runtime.sendMessage({ type: 'stop-capture', tabId: tab.id });
    } else {
      // Allowed because opening this popup counts as invoking the extension on the tab.
      const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
      result = await chrome.runtime.sendMessage({ type: 'start-capture', tabId: tab.id, streamId });
    }
    if (result && result.ok === false) fail(result.error);
  } catch (error) {
    fail(error.message || error);
  }
  refresh();
});

for (const box of switches) {
  box.addEventListener('change', () => chrome.storage.local.set({ [box.id]: box.checked }));
}
for (const radio of document.querySelectorAll('input[name="size"]')) {
  radio.addEventListener('change', () => chrome.storage.local.set({ size: radio.value }));
}

(async () => {
  const stored = await chrome.storage.local.get({ enabled: true, always: false, flip: false, size: 'medium' });
  for (const box of switches) box.checked = stored[box.id];
  document.querySelector(`input[name="size"][value="${stored.size}"]`).checked = true;
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  await refresh();
  setInterval(refresh, 500);
})();
