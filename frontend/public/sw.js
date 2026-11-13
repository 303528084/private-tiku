/*
 * 私有护理题库系统 Service Worker（v1.4 T05）
 *
 * ===========================================================================
 * 安全边界（硬约束，写错等于击穿 T01 访问门禁）
 * ===========================================================================
 * **绝对不缓存 `/api/*`**。
 *
 * 理由：`/api/search` 的响应体就是完整题库内容（题干/选项/答案/解析）。如果 SW
 * 把它写进 Cache Storage，那么**同一个浏览器登出后**仍能从缓存里读到题目——
 * T01 的口令闸门只拦网络请求，拦不住缓存读取，整个门禁直接作废。
 *
 * 因此 fetch 处理器对 `/api/` 一律**直接 return**（不调用 `respondWith`），
 * 让请求原样走网络，浏览器缓存策略也交回 HTTP 头控制。
 * `test_pwa.py` 里有一条静态断言扫描本文件，确保没有任何 `/api` 的 cache.put。
 *
 * 缓存策略：
 *   - 导航请求（mode === 'navigate'）：network-first，3 秒超时后回退缓存的 `/`。
 *     这样升级后能立刻拿到新壳，断网时也有壳可显示。
 *   - 静态资源（/assets/、/icons/、/favicon.*、/apple-touch-icon.png、
 *     /manifest.webmanifest）：cache-first + 后台 revalidate（stale-while-revalidate）。
 *     `/assets/` 下的文件名带内容 hash，可以放心长期缓存。
 *   - 只缓存 `response.ok && response.type === 'basic'` 的响应：
 *     401/404/500 一律不进缓存，避免把错误页固化下来。
 * ===========================================================================
 */

/* 缓存名带版本号：改版本 → activate 阶段自动清掉旧缓存。改版时手动 bump。 */
const CACHE_NAME = 'tiku-static-v1';

/** 导航请求的 network-first 超时（毫秒）。超时即用缓存的壳，保证弱网可用。 */
const NAVIGATION_TIMEOUT_MS = 3000;

/** 安装时预缓存的最小集合：壳 + manifest + 两个 192/512 图标。 */
const PRECACHE_URLS = ['/', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png'];

/** 走 cache-first 的静态资源前缀（`/assets/` 下文件名带 hash，可长缓存）。 */
const STATIC_PREFIXES = ['/assets/', '/icons/'];

/** 走 cache-first 的静态资源精确路径。 */
const STATIC_PATHS = [
  '/favicon.svg',
  '/favicon.ico',
  '/apple-touch-icon.png',
  '/manifest.webmanifest',
];

/**
 * 判断响应是否允许写入缓存。
 *
 * `response.type === 'basic'` 排除 opaque 响应（跨域 no-cors），这类响应
 * 连状态码都读不到，缓存下来只会污染；`response.ok` 排除 4xx/5xx。
 *
 * @param {Response} response 待判断的响应。
 * @returns {boolean} true 表示可以缓存。
 */
function isCacheable(response) {
  return Boolean(response) && response.ok && response.type === 'basic';
}

/**
 * 判断是否是需要 cache-first 的静态资源。
 *
 * @param {string} pathname 请求路径。
 * @returns {boolean} true 表示走静态资源策略。
 */
function isStaticAsset(pathname) {
  if (STATIC_PATHS.includes(pathname)) {
    return true;
  }
  return STATIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

/**
 * 带超时的 fetch。
 *
 * @param {Request} request 原始请求。
 * @param {number} timeoutMs 超时毫秒数。
 * @returns {Promise<Response>} 网络响应；超时则 reject。
 */
async function fetchWithTimeout(request, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(request, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 导航请求：network-first，失败或超时回退缓存的 SPA 壳。
 *
 * @param {Request} request 导航请求。
 * @returns {Promise<Response>} 响应。
 */
async function handleNavigation(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetchWithTimeout(request, NAVIGATION_TIMEOUT_MS);
    // 所有前端路由都由后端回退到同一份 index.html，因此统一以 '/' 为键存壳
    if (isCacheable(response)) {
      await cache.put('/', response.clone());
    }
    return response;
  } catch (error) {
    const cached = await cache.match('/');
    if (cached) {
      return cached;
    }
    throw error;
  }
}

/**
 * 静态资源：cache-first + 后台 revalidate。
 *
 * @param {Request} request 静态资源请求。
 * @returns {Promise<Response>} 响应。
 */
async function handleStaticAsset(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);

  const networkPromise = fetch(request)
    .then((response) => {
      if (isCacheable(response)) {
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    // 命中缓存立刻返回，网络请求在后台完成并写回（stale-while-revalidate）
    return cached;
  }
  const fresh = await networkPromise;
  if (fresh) {
    return fresh;
  }
  return new Response('', { status: 504, statusText: 'Gateway Timeout' });
}

/**
 * 安装：预缓存最小集合后立即接管。
 *
 * 单个资源失败**不阻断**整个安装（例如 dist 里暂时缺某个图标），否则 SW 会
 * 一直停在 installing，用户永远拿不到离线能力。
 *
 * @param {ExtendableEvent} event install 事件。
 */
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      await Promise.allSettled(
        PRECACHE_URLS.map(async (url) => {
          // cache: 'reload' 绕过 HTTP 缓存，确保拿到的是本次部署的新产物
          const response = await fetch(new Request(url, { cache: 'reload' }));
          if (!isCacheable(response)) {
            return;
          }
          // 壳必须是 HTML：前端未构建时后端会返回一段 JSON 提示，
          // 那种响应缓存成 '/' 会让离线打开时看到 JSON 而不是应用
          if (url === '/' && !(response.headers.get('content-type') || '').includes('text/html')) {
            return;
          }
          await cache.put(url, response);
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

/**
 * 激活：清掉所有非当前版本的缓存，并立即接管已打开的页面。
 *
 * @param {ExtendableEvent} event activate 事件。
 */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)));
      await self.clients.claim();
    })(),
  );
});

/**
 * 请求拦截：按上面的安全边界与缓存策略分流。
 *
 * @param {FetchEvent} event fetch 事件。
 */
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // 1) 只处理同源 GET：POST/PUT/DELETE 一律直连（它们本来也不该被缓存）
  if (request.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // 2) API 一律不拦截——缓存 /api/* 会让登出后仍能读到题库内容（见文件头）
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // 3) SW 自身不缓存，否则用户永远拿不到新版本
  if (url.pathname === '/sw.js') {
    return;
  }

  // 4) 导航请求 → network-first
  if (request.mode === 'navigate') {
    event.respondWith(handleNavigation(request));
    return;
  }

  // 5) 静态资源 → cache-first + 后台 revalidate
  if (isStaticAsset(url.pathname)) {
    event.respondWith(handleStaticAsset(request));
  }

  // 其余请求（如 /docs、/openapi.json）不拦截，直连网络
});
