// FINN service worker: shows push notifications (even when the app is closed)
// and opens the right screen when one is tapped.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "FINN", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "FINN", {
      body: data.body || "",
      tag: data.tag, // a newer alert with the same tag replaces the old one
      renotify: Boolean(data.tag),
      icon: "/icon.svg",
      badge: "/icon.svg",
      data: { url: data.url || "/alerts" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/alerts", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      // Reuse an open FINN tab if there is one.
      for (const client of list) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
