// Runs in the page's own world. Players built with `new Audio()` or inside
// shadow DOM never fire a `play` event the content script can see, so announce
// every play() call with the element attached to the event.
(() => {
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    try {
      if (this.isConnected) {
        this.dispatchEvent(new CustomEvent('spongebeat:media', { bubbles: true, composed: true }));
      } else {
        document.dispatchEvent(new MouseEvent('spongebeat:media', { relatedTarget: this }));
      }
    } catch {}
    return play.apply(this, args);
  };
})();
