// Service Worker：只为 Web Push 存在。不做离线缓存——聊天内容全在服务端，
// 缓存一份过期的对话比没有更糟。
//
// 装到主屏之后 iOS 才会把这个 SW 保活并投递 push（Safari 标签页里拿不到订阅）。

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = { title: 'nostos', body: '', url: '/' };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (_) {
    if (event.data) data.body = event.data.text();
  }
  // userVisibleOnly 订阅必须弹一条，静默收下会被浏览器判违约、多次之后吊销订阅。
  event.waitUntil(
    self.registration.showNotification(data.title || 'nostos', {
      body: data.body || '',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url: data.url || '/' },
      // 同一个伙伴的消息合并成一条，别在锁屏上堆成一列
      tag: 'nostos-wake',
      renotify: true,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if ('focus' in w) return w.focus();   // 已经开着就聚焦，别再开一个
    }
    if (self.clients.openWindow) return self.clients.openWindow(url);
  })());
});
