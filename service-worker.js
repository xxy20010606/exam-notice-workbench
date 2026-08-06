// exam-notice PWA Service Worker (v2)
// 修复：新版本立即生效（skipWaiting + clients.claim），手机端打开即刷新，不再卡旧缓存
const CACHE = 'exam-notice-v2';
const CORE = ['./', './index.html', './manifest.webmanifest', './icon.svg'];

self.addEventListener('install', event => {
  self.skipWaiting(); // 安装后立刻激活，不等所有页面关闭
  event.waitUntil(
    caches.open(CACHE).then(c => c.addAll(CORE).catch(() => {}))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim()) // 立即接管所有已打开的页面
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === 'navigate') {
    // 导航请求：网络优先，成功则更新缓存，失败回退缓存
    event.respondWith(
      fetch(req)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put('./index.html', copy));
          return res;
        })
        .catch(() => caches.match('./index.html').then(r => r || caches.match('./')))
    );
  } else {
    // 静态资源：缓存优先，缺失再网络拉取并缓存
    event.respondWith(
      caches.match(req).then(r =>
        r || fetch(req).then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
          return res;
        }).catch(() => r)
      )
    );
  }
});
