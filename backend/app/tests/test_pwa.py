"""PWA 壳、应用图标与静态资源 MIME 的回归测试（v1.4 T05）。

覆盖四件事：

1. **图标**：4 个 PNG 真实存在、尺寸正确、是 RGBA。尺寸直接读 PNG 的 IHDR 块，
   不依赖 Pillow（Pillow 只是生成脚本的开发期依赖，运行期不需要）。
2. **manifest**：与磁盘上的图标严格一致（``src`` 真实存在、``sizes`` 与真实像素
   相符、``purpose`` 覆盖 any + maskable），描述文案与 ``index.html`` 的 meta 一致。
3. **Service Worker 的安全边界**：``/api/*`` 绝不能被拦截、更不能被缓存——缓存了
   就意味着登出后仍能从 Cache Storage 读到题库内容，T01 访问闸门整个作废。
   这条用 ``node:vm`` **真跑**一遍 ``sw.js`` 的 fetch 处理器来验证（见
   ``js/sw_harness.mjs``），而不是 grep 源码：源码里的注释也会提到 ``/api/``，
   grep 既会误报也会漏报。
4. **MIME**：``/manifest.webmanifest`` 必须是 ``application/manifest+json``，
   ``/sw.js`` 必须是 ``text/javascript`` 且带 ``Cache-Control: no-cache``。
   容器里是 Python 3.11，它的 ``mimetypes`` 不认识 ``.webmanifest``，不显式注册
   就会退回 ``application/octet-stream``，Chrome 直接拒绝安装 PWA。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from app import config
from app.tests.asgi_utils import asgi_get

REPO_ROOT = Path(__file__).resolve().parents[3]
"""仓库根目录（``backend/app/tests`` 往上三层）。"""

PUBLIC_DIR = REPO_ROOT / "frontend" / "public"
ICONS_DIR = PUBLIC_DIR / "icons"
MANIFEST_PATH = PUBLIC_DIR / "manifest.webmanifest"
SERVICE_WORKER_PATH = PUBLIC_DIR / "sw.js"
INDEX_HTML_PATH = REPO_ROOT / "frontend" / "index.html"
DIST_DIR = REPO_ROOT / "frontend" / "dist"
HARNESS_PATH = Path(__file__).resolve().parent / "js" / "sw_harness.mjs"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
"""PNG 文件魔数。"""

ICON_CASES: Tuple[Tuple[str, int, str], ...] = (
    ("icon-192.png", 192, "any"),
    ("icon-512.png", 512, "any"),
    ("icon-192-maskable.png", 192, "maskable"),
    ("icon-512-maskable.png", 512, "maskable"),
)
"""``(文件名, 边长, purpose)``：PWA 安装的最低要求是 192 与 512 各一个。"""


def read_png_size(path: Path) -> Tuple[int, int, int, int]:
    """从 PNG 的 IHDR 块读出尺寸与像素格式。

    Args:
        path: PNG 文件路径。

    Returns:
        ``(width, height, bit_depth, color_type)``。

    Raises:
        AssertionError: 文件不是合法 PNG 或缺少 IHDR。
    """
    data = path.read_bytes()
    assert data[:8] == PNG_SIGNATURE, f"{path.name} 不是 PNG 文件"
    assert data[12:16] == b"IHDR", f"{path.name} 缺少 IHDR 块"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height, data[24], data[25]


@pytest.fixture(scope="module")
def dist_dir() -> Path:
    """前端构建产物目录；未构建时跳过（并给出可执行的修复命令）。"""
    if not (DIST_DIR / "index.html").is_file():
        pytest.skip("frontend/dist 未构建，跳过静态资源相关断言：cd frontend && npm run build")
    return DIST_DIR


@pytest.fixture(scope="module")
def sw_behavior() -> Dict[str, Any]:
    """在 node 里真跑一遍 ``sw.js``，返回各场景的行为快照。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("未安装 Node.js，跳过 Service Worker 行为验证")
    completed = subprocess.run(
        [node, str(HARNESS_PATH), str(SERVICE_WORKER_PATH)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"harness 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


# ===========================================================================
# 1) 图标
# ===========================================================================
@pytest.mark.parametrize("name,size,purpose", ICON_CASES)
def test_icon_png_has_expected_size(name: str, size: int, purpose: str) -> None:
    """图标真实存在、边长正确、是 8 位 RGBA（``purpose`` 仅用于参数化可读性）。"""
    path = ICONS_DIR / name
    assert path.is_file(), f"缺少图标：{path}（运行 python scripts/generate_pwa_icons.py 生成）"

    width, height, bit_depth, color_type = read_png_size(path)
    assert (width, height) == (size, size), f"{name} 尺寸是 {width}x{height}，应为 {size}x{size}"
    assert bit_depth == 8, f"{name} 位深是 {bit_depth}，应为 8"
    assert color_type == 6, f"{name} 颜色类型是 {color_type}，应为 6（RGBA）"
    assert path.stat().st_size > 500, f"{name} 只有 {path.stat().st_size} 字节，疑似空白图"


def test_icons_are_not_upscaled_copies() -> None:
    """512 图标不能是 192 / apple-touch-icon 的简单放大。

    判据不是字节大小（模糊图也可能很大），而是**边缘锐度**：真按 4 倍超采样重绘的
    图标，在 512 尺寸下圆角边缘仍有明显的 alpha 过渡带；把 180px 图放大 2.8 倍则会
    出现大片半透明糊边。这里统计「半透明像素」占比，超过 12% 视为放大产物。
    """
    image_module = pytest.importorskip("PIL.Image", reason="需要 Pillow 才能做像素级校验")
    with image_module.open(ICONS_DIR / "icon-512.png") as icon:
        alpha = icon.convert("RGBA").getchannel("A")
    histogram = alpha.histogram()
    total = sum(histogram)
    # 完全透明（0）是圆角外的区域；完全实心（255）是主体；其余算过渡带
    partial = total - histogram[0] - histogram[255]
    ratio = partial / total
    assert ratio < 0.12, f"半透明过渡像素占比 {ratio:.1%}，疑似由小图放大而来"


def test_maskable_icon_is_full_bleed_and_any_icon_is_rounded() -> None:
    """maskable 版背景铺满整块，``any`` 版四角透明。"""
    image_module = pytest.importorskip("PIL.Image", reason="需要 Pillow 才能做像素级校验")

    with image_module.open(ICONS_DIR / "icon-512.png") as any_icon:
        any_rgba = any_icon.convert("RGBA")
    with image_module.open(ICONS_DIR / "icon-512-maskable.png") as maskable_icon:
        maskable_rgba = maskable_icon.convert("RGBA")

    corner = (2, 2)
    assert any_rgba.getpixel(corner)[3] == 0, "any 版四角应当透明（圆角由 PNG 自带）"
    assert maskable_rgba.getpixel(corner)[3] == 255, "maskable 版背景必须铺满，四角不透明"


def test_icon_gradient_runs_top_to_bottom() -> None:
    """底色是 ``#1f6feb`` → ``#1559c0`` 的**垂直**渐变，不是纯色蒙混。"""
    image_module = pytest.importorskip("PIL.Image", reason="需要 Pillow 才能做像素级校验")

    with image_module.open(ICONS_DIR / "icon-512-maskable.png") as icon:
        rgba = icon.convert("RGB")
    width, height = rgba.size
    top = rgba.getpixel((4, 4))
    bottom = rgba.getpixel((4, height - 5))
    assert sum(top) > sum(bottom), f"渐变方向不对：顶部 {top} 应比底部 {bottom} 亮"
    # 与设计稿的起止色相比，允许 ±8 的插值/量化误差
    for actual, expected in ((top, (0x1F, 0x6F, 0xEB)), (bottom, (0x15, 0x59, 0xC0))):
        for got, want in zip(actual, expected):
            assert abs(got - want) <= 8, f"渐变色偏差过大：{actual} vs {expected}"


# ===========================================================================
# 2) manifest
# ===========================================================================
@pytest.fixture(scope="module")
def manifest() -> Dict[str, Any]:
    """读取并解析 manifest。"""
    assert MANIFEST_PATH.is_file(), f"缺少 manifest：{MANIFEST_PATH}"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_required_fields(manifest: Dict[str, Any]) -> None:
    """安装 PWA 必需的字段齐全且取值合法。"""
    assert manifest["name"] == "私有护理题库系统"
    assert manifest["short_name"] == "护理题库"
    assert manifest["lang"] == "zh-CN"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == "#1f6feb"
    assert manifest["background_color"] == "#f5f7fb"


def test_manifest_short_name_fits_under_desktop_icon(manifest: Dict[str, Any]) -> None:
    """桌面图标下方最多显示约 12 个字符，``short_name`` 不能更长。"""
    assert len(manifest["short_name"]) <= 12, manifest["short_name"]


def test_manifest_description_matches_index_html(manifest: Dict[str, Any]) -> None:
    """manifest 与 ``index.html`` 的 description 必须一致（同一份文案，改一处即可）。"""
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    matched = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
    assert matched, "index.html 里找不到 description meta"
    assert manifest["description"] == matched.group(1)


def test_manifest_icons_match_files_on_disk(manifest: Dict[str, Any]) -> None:
    """每个图标的 ``src`` 真实存在，且 ``sizes`` 与真实像素相符。"""
    icons = manifest["icons"]
    assert len(icons) == 4, icons

    declared: Dict[str, Tuple[int, int, str]] = {}
    for entry in icons:
        src = entry["src"]
        assert src.startswith("/"), f"图标路径必须是绝对路径：{src}"
        path = PUBLIC_DIR / src.lstrip("/")
        assert path.is_file(), f"manifest 引用了不存在的图标：{src}"

        width, height, _, _ = read_png_size(path)
        assert entry["sizes"] == f"{width}x{height}", (
            f"{src} 声明 {entry['sizes']}，实际 {width}x{height}"
        )
        assert entry["type"] == "image/png"
        declared[src] = (width, height, entry["purpose"])

    for size in (192, 512):
        assert f"/icons/icon-{size}.png" in declared, f"缺少 {size} 的 any 图标"
        assert f"/icons/icon-{size}-maskable.png" in declared, f"缺少 {size} 的 maskable 图标"
        assert declared[f"/icons/icon-{size}.png"][2] == "any"
        assert declared[f"/icons/icon-{size}-maskable.png"][2] == "maskable"


def test_index_html_links_manifest_and_keeps_favicons() -> None:
    """``index.html`` 挂了 manifest，且原有三行 favicon 未被覆盖掉。"""
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/manifest.webmanifest" />' in html
    assert 'href="/favicon.svg"' in html
    assert 'href="/favicon.ico"' in html
    assert 'href="/apple-touch-icon.png"' in html


# ===========================================================================
# 3) Service Worker 安全边界（真跑一遍）
# ===========================================================================
def test_service_worker_never_intercepts_api(sw_behavior: Dict[str, Any]) -> None:
    """``/api/*`` 一律不 ``respondWith``，让请求直连网络。

    这是本批最重要的一条断言：一旦 SW 拦截并缓存 ``/api/search``，用户登出后仍能
    从 Cache Storage 读到完整题库内容，T01 访问闸门等于不存在。
    """
    for scenario in ("apiGet", "apiPost", "apiPrefixedPath", "apiNavigation"):
        assert sw_behavior[scenario]["respondWithCalled"] is False, (
            f"{scenario} 被 Service Worker 拦截了：{sw_behavior[scenario]}"
        )


def test_service_worker_ignores_cross_origin_and_itself(sw_behavior: Dict[str, Any]) -> None:
    """跨域请求与 ``sw.js`` 自身都不拦截。"""
    assert sw_behavior["crossOrigin"]["respondWithCalled"] is False
    assert sw_behavior["serviceWorkerSelf"]["respondWithCalled"] is False


def test_service_worker_caches_static_assets_only(sw_behavior: Dict[str, Any]) -> None:
    """静态资源走 cache-first 并写入缓存，且缓存里**没有**任何 ``/api/`` 路径。"""
    assert sw_behavior["staticAsset"]["respondWithCalled"] is True
    assert sw_behavior["staticAsset"]["status"] == 200
    assert any(
        url.endswith("/assets/index-abc123.js") for url in sw_behavior["staticAssetCachePuts"]
    ), sw_behavior["staticAssetCachePuts"]

    for key in ("staticAssetCachePuts", "navigationCachePuts", "precachedUrls"):
        for url in sw_behavior[key]:
            assert "/api/" not in url, f"{key} 里出现了 API 路径：{url}"


def test_service_worker_handles_navigation(sw_behavior: Dict[str, Any]) -> None:
    """导航请求被接管（network-first），成功时把壳写入 ``/``。

    缓存键是相对路径 ``/``：SW 用 ``cache.put('/')`` 写入，``cache.match('/')`` 读取，
    浏览器会按 SW 的 scope 解析，两者一致。
    """
    assert sw_behavior["navigation"]["respondWithCalled"] is True
    assert sw_behavior["navigation"]["status"] == 200
    assert "/" in sw_behavior["navigationCachePuts"]


def test_service_worker_does_not_cache_error_responses(sw_behavior: Dict[str, Any]) -> None:
    """非 2xx 响应不进缓存（否则错误页会被固化，刷新也不恢复）。"""
    assert sw_behavior["staticNotFound"]["respondWithCalled"] is True
    assert sw_behavior["staticNotFound"]["status"] == 404
    assert sw_behavior["staticNotFoundCached"] is False


def test_service_worker_precache_set_is_minimal(sw_behavior: Dict[str, Any]) -> None:
    """安装时只预缓存壳 + manifest + 两个图标，且不含 API。"""
    precached = set(sw_behavior["precachedUrls"])
    assert precached == {
        "/",
        "/manifest.webmanifest",
        "/icons/icon-192.png",
        "/icons/icon-512.png",
    }, precached


def test_service_worker_precache_skips_non_html_root(sw_behavior: Dict[str, Any]) -> None:
    """前端未构建时 ``/`` 会返回 JSON 提示，这种响应不能被当成 SPA 壳缓存。"""
    cached = set(sw_behavior["precachedWhenRootIsJson"])
    assert "/" not in cached, cached
    assert "/manifest.webmanifest" in cached, cached


def test_service_worker_activate_clears_old_caches(sw_behavior: Dict[str, Any]) -> None:
    """``activate`` 只保留当前版本的缓存名。"""
    assert set(sw_behavior["deletedCaches"]) == {"tiku-static-v0", "random-cache"}, (
        sw_behavior["deletedCaches"]
    )


def test_service_worker_cache_name_is_versioned() -> None:
    """缓存名必须带版本号，否则 ``activate`` 无法区分新旧。"""
    source = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
    matched = re.search(r"const\s+CACHE_NAME\s*=\s*'([^']+)'", source)
    assert matched, "sw.js 里找不到 CACHE_NAME"
    assert re.search(r"-v\d+$", matched.group(1)), f"缓存名缺少版本号：{matched.group(1)}"


# ===========================================================================
# 4) 静态资源的 MIME 与缓存头
# ===========================================================================
def test_manifest_is_served_with_correct_content_type(dist_dir: Path) -> None:
    """``/manifest.webmanifest`` → ``application/manifest+json``。

    容器里是 Python 3.11，其 mimetypes 不认识 ``.webmanifest``；不显式注册就会退回
    ``application/octet-stream``，Chrome 判定 manifest 无效并拒绝安装。
    """
    status, headers, body = asgi_get("/manifest.webmanifest")

    assert status == 200, body[:200]
    assert headers.get("content-type", "").startswith("application/manifest+json"), headers
    assert json.loads(body.decode("utf-8"))["name"] == "私有护理题库系统"


def test_service_worker_is_served_uncached(dist_dir: Path) -> None:
    """``/sw.js`` → ``text/javascript`` + ``Cache-Control: no-cache``。

    少了 ``no-cache``，浏览器会长期复用旧 SW，用户永远拿不到新版本。
    """
    status, headers, body = asgi_get("/sw.js")

    assert status == 200, body[:200]
    assert headers.get("content-type", "").startswith("text/javascript"), headers
    assert "no-cache" in headers.get("cache-control", ""), headers
    assert b"tiku-static-v" in body


def test_icons_are_served_as_png(dist_dir: Path) -> None:
    """图标按 ``image/png`` 返回，且字节与源文件一致。"""
    status, headers, body = asgi_get("/icons/icon-192.png")

    assert status == 200, body[:200]
    assert headers.get("content-type", "").startswith("image/png"), headers
    assert body == (ICONS_DIR / "icon-192.png").read_bytes()


def test_pwa_files_stay_reachable_when_gate_enabled(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门禁开启时 manifest 与 sw.js 仍可匿名访问。

    否则未登录的浏览器拿不到 manifest（无法安装）、也注册不了 SW（无法离线），
    而这两个文件本身不含任何题库数据。
    """
    monkeypatch.setenv(config.ACCESS_TOKEN_ENV, "pwa-test-token")

    for path, expected in (
        ("/manifest.webmanifest", "application/manifest+json"),
        ("/sw.js", "text/javascript"),
    ):
        status, headers, body = asgi_get(path)
        assert status == 200, (path, status, body[:120])
        assert headers.get("content-type", "").startswith(expected), (path, headers)

    # 对照组：同一时刻 /api/* 仍然被拦住，证明门禁本身是生效的
    status, _, _ = asgi_get("/api/stats")
    assert status == 401
