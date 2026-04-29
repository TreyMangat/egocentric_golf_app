// Minimal service worker — exists primarily so iOS treats this as installable.
// We intentionally do NOT cache pages or API responses; the capture flow is
// real-time and stale data would be confusing.

const VERSION = "swing-pipeline-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Pass-through fetch handler. Lets the page work offline only insofar as the
// browser already cached it; we don't add anything extra.
self.addEventListener("fetch", (event) => {
  // No-op: rely on browser default.
  return;
});
