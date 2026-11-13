// Service Worker 行为验证器（v1.4 T05 测试专用，不是产品代码）
//
// 用法：
//   node sw_harness.mjs <sw.js 路径>
//
// 输出：stdout 一行 JSON，描述各个场景下 respondWith 是否被调用、缓存写了什么。
//
// 为什么用 node:vm 而不是真浏览器：
// 要在 pytest 里验证的是「安全边界」这一条纯逻辑性质——`/api/*` 绝不能被拦截、
// 更不能被缓存。起一个真 Chrome 只为断言这一点成本过高；而用 vm 构造最小 SW
// 全局环境（self / caches / fetch）就能精确驱动 fetch 处理器，且可断言缓存内容。
//
// 真浏览器里的安装性 / 缓存内容由人工验收（DevTools → Application），
// 本脚本只负责「不写错代码」这一层。

import fs from 'node:fs';
import vm from 'node:vm';

/** 伪造 Service Worker 的源站，所有相对 URL 都按它解析。 */
const ORIGIN = 'http://127.0.0.1:8000';

/** 伪造 Response：只需要 SW 代码用到的那几个字段。 */
class FakeResponse {
  constructor(body, init = {}) {
    this.body = body;
    this.status = init.status ?? 200;
    this.statusText = init.statusText ?? '';
    this.ok = this.status >= 200 && this.status < 300;
    this.type = init.type ?? 'basic';
    this.headers = new Map(Object.entries(init.headers ?? {}));
  }

  clone() {
    const copy = new FakeResponse(this.body, {
      status: this.status,
      statusText: this.statusText,
      type: this.type,
    });
    copy.headers = new Map(this.headers);
    return copy;
  }
}

/** 伪造 Request：SW 代码只读 url / method / mode。

真实浏览器里 ``new Request('/')`` 会按 SW 脚本的 URL 解析成绝对地址，这里用
``ORIGIN`` 复刻该行为——否则 ``fetch('/')`` 拿到的 url 是 ``'/'``，
与按绝对地址配置的 fetch 桩对不上，会静默走默认响应、测出假结果。
*/
class FakeRequest {
  constructor(url, init = {}) {
    this.url = new URL(String(url), ORIGIN).href;
    this.method = init.method ?? 'GET';
    this.mode = init.mode ?? 'no-cors';
    this.cache = init.cache;
  }
}

/**
 * 构造一个最小 Service Worker 运行环境。
 *
 * @param {string} source sw.js 源码。
 * @returns {object} 环境句柄。
 */
function createEnvironment(source) {
  const handlers = new Map();
  const caches_ = new Map();
  const cachePuts = [];
  const deletedCaches = [];
  const state = {
    existingCacheKeys: [],
    /** 按 URL 前缀决定 fetch 的返回；默认 200 + basic。 */
    fetchResponses: new Map(),
    fetchCalls: [],
  };

  const origin = ORIGIN;

  const selfStub = {
    location: { origin },
    addEventListener(type, handler) {
      handlers.set(type, handler);
    },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };

  const cachesApi = {
    keys: async () => state.existingCacheKeys.slice(),
    delete: async (name) => {
      deletedCaches.push(name);
      caches_.delete(name);
      return true;
    },
    open: async (name) => {
      if (!caches_.has(name)) {
        caches_.set(name, new Map());
      }
      const store = caches_.get(name);
      return {
        match: async (request) => store.get(typeof request === 'string' ? request : request.url),
        put: async (request, response) => {
          const url = typeof request === 'string' ? request : request.url;
          store.set(url, response);
          cachePuts.push({ cacheName: name, url });
        },
        keys: async () => [...store.keys()],
      };
    },
  };

  const fetchStub = async (request) => {
    const url = typeof request === 'string' ? request : request.url;
    state.fetchCalls.push(url);
    for (const [prefix, response] of state.fetchResponses) {
      if (url.startsWith(prefix)) {
        return response;
      }
    }
    return new FakeResponse('', { headers: { 'content-type': 'text/html; charset=utf-8' } });
  };

  const sandbox = {
    self: selfStub,
    caches: cachesApi,
    fetch: fetchStub,
    URL,
    Request: FakeRequest,
    Response: FakeResponse,
    AbortController,
    setTimeout,
    clearTimeout,
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'sw.js' });

  /**
   * 派发一个 fetch 事件，返回是否调用 respondWith 以及最终状态码。
   *
   * @param {object} requestInit FakeRequest 的构造参数。
   * @returns {Promise<object>} { respondWithCalled, status }。
   */
  async function dispatchFetch(requestInit) {
    const event = {
      request: new FakeRequest(requestInit.url, requestInit),
      respondWithCalled: false,
      promise: null,
      respondWith(promise) {
        this.respondWithCalled = true;
        this.promise = promise;
      },
    };
    const handler = handlers.get('fetch');
    if (handler) {
      handler(event);
    }
    let status = null;
    if (event.respondWithCalled && event.promise) {
      try {
        const response = await event.promise;
        status = response ? response.status : null;
      } catch {
        status = 'error';
      }
    }
    return { respondWithCalled: event.respondWithCalled, status };
  }

  /**
   * 触发 install / activate 生命周期事件。
   *
   * @param {string} type 事件类型。
   * @returns {Promise<void>} 事件里的 waitUntil 完成后 resolve。
   */
  async function dispatchLifecycle(type) {
    const pending = [];
    const event = {
      waitUntil(promise) {
        pending.push(promise);
      },
    };
    const handler = handlers.get(type);
    if (handler) {
      handler(event);
    }
    await Promise.all(pending);
  }

  return { origin, cachePuts, deletedCaches, state, dispatchFetch, dispatchLifecycle };
}

/**
 * 跑完全部场景，返回可被 pytest 断言的结果对象。
 *
 * @param {string} source sw.js 源码。
 * @returns {Promise<object>} 结果。
 */
async function run(source) {
  const env = createEnvironment(source);
  const { origin } = env;
  const results = {};

  // ---- 安全边界：这些请求一律不能 respondWith ----
  results.apiGet = await env.dispatchFetch({
    url: `${origin}/api/search?q=1`,
    method: 'GET',
    mode: 'cors',
  });
  results.apiPost = await env.dispatchFetch({
    url: `${origin}/api/auth/logout`,
    method: 'POST',
    mode: 'cors',
  });
  results.apiPrefixedPath = await env.dispatchFetch({
    url: `${origin}/api/questions/1`,
    method: 'GET',
    mode: 'no-cors',
  });
  // 关键用例：地址栏直接打开 /api/... 时 mode 是 navigate。少了 /api 守卫，
  // network-first 会把这份 JSON 响应当成 SPA 壳写进 '/'，之后离线打开首页
  // 看到的就是一段 JSON——而缓存里也多了一份题库数据。
  results.apiNavigation = await env.dispatchFetch({
    url: `${origin}/api/search?q=1`,
    method: 'GET',
    mode: 'navigate',
  });
  results.crossOrigin = await env.dispatchFetch({
    url: 'https://cdn.example.com/lib.js',
    method: 'GET',
    mode: 'cors',
  });
  results.serviceWorkerSelf = await env.dispatchFetch({
    url: `${origin}/sw.js`,
    method: 'GET',
    mode: 'same-origin',
  });

  // ---- 静态资源：cache-first，应当 respondWith 并写入缓存 ----
  env.cachePuts.length = 0;
  results.staticAsset = await env.dispatchFetch({
    url: `${origin}/assets/index-abc123.js`,
    method: 'GET',
    mode: 'no-cors',
  });
  results.staticAssetCachePuts = env.cachePuts.map((entry) => entry.url);

  results.icon = await env.dispatchFetch({
    url: `${origin}/icons/icon-192.png`,
    method: 'GET',
    mode: 'no-cors',
  });

  // ---- 导航请求：network-first ----
  results.navigation = await env.dispatchFetch({
    url: `${origin}/questions/1`,
    method: 'GET',
    mode: 'navigate',
  });
  results.navigationCachePuts = env.cachePuts.map((entry) => entry.url);

  // ---- 非 2xx 响应不允许进缓存 ----
  env.state.fetchResponses.set(
    `${origin}/assets/broken.js`,
    new FakeResponse('', { status: 404 }),
  );
  const putsBefore = env.cachePuts.length;
  results.staticNotFound = await env.dispatchFetch({
    url: `${origin}/assets/broken.js`,
    method: 'GET',
    mode: 'no-cors',
  });
  results.staticNotFoundCached = env.cachePuts.length > putsBefore;

  // ---- install：预缓存集合 ----
  env.cachePuts.length = 0;
  await env.dispatchLifecycle('install');
  results.precachedUrls = env.cachePuts.map((entry) => entry.url);

  // 前端未构建时后端会给 '/' 返回 JSON 提示，这种响应不能被当成壳缓存
  env.cachePuts.length = 0;
  env.state.fetchResponses.set(
    `${origin}/`,
    new FakeResponse('{"code":0}', { headers: { 'content-type': 'application/json' } }),
  );
  await env.dispatchLifecycle('install');
  results.precachedWhenRootIsJson = env.cachePuts.map((entry) => entry.url);

  // ---- activate：清掉旧版本缓存 ----
  env.state.existingCacheKeys = ['tiku-static-v1', 'tiku-static-v0', 'random-cache'];
  env.deletedCaches.length = 0;
  await env.dispatchLifecycle('activate');
  results.deletedCaches = env.deletedCaches.slice();

  return results;
}

const swPath = process.argv[2];
if (!swPath) {
  console.error('用法：node sw_harness.mjs <sw.js 路径>');
  process.exit(2);
}
const source = fs.readFileSync(swPath, 'utf8');
run(source)
  .then((results) => {
    process.stdout.write(JSON.stringify(results));
  })
  .catch((error) => {
    console.error(error && error.stack ? error.stack : String(error));
    process.exit(1);
  });
