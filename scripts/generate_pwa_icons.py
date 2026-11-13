"""生成 PWA 应用图标（v1.4 T05）。

按 ``frontend/public/favicon.svg`` 的设计用 Pillow **重绘**，而不是把
``apple-touch-icon.png``（180×180）放大——放大会糊掉边缘与对勾。

产出 4 个文件到 ``frontend/public/icons/``：

* ``icon-192.png`` / ``icon-512.png``：``purpose: any``，圆角透明背景
* ``icon-192-maskable.png`` / ``icon-512-maskable.png``：``purpose: maskable``，
  背景铺满整块（由系统裁切形状），图形元素缩到中心 60% 区域内

设计坐标（viewBox 64×64）：
* 底色：``rect rx=14`` 垂直渐变 ``#1f6feb`` → ``#1559c0``
* 白色书页：``x=14 y=9 w=36 h=46 rx=5.5``
* 蓝色对勾：``M21 31.5 L28.5 39.5 L43 22``，``stroke=#1f6feb``，``stroke-width=7.6``

**超采样**：统一按目标尺寸的 4 倍绘制，再用 ``LANCZOS`` 缩到目标尺寸，
否则圆角与斜线会有明显锯齿。缩小前转 ``RGBa``（预乘 alpha）以避免透明
边缘出现黑色描边。

用法::

    python scripts/generate_pwa_icons.py

脚本是幂等的：重复运行产出完全相同的字节（无随机源）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final, Tuple

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# 常量：全部来自 favicon.svg 的设计坐标（viewBox 64×64）
# ---------------------------------------------------------------------------
VIEWBOX: Final[float] = 64.0
"""设计视图边长。所有下面的坐标都是这个坐标系里的值。"""

SUPERSAMPLE: Final[int] = 4
"""超采样倍数：先按 N 倍画，再 LANCZOS 缩回目标尺寸。"""

GRADIENT_TOP: Final[Tuple[int, int, int]] = (0x1F, 0x6F, 0xEB)
"""渐变起始色 #1f6feb（与 index.html 的 theme-color、MUI primary 一致）。"""

GRADIENT_BOTTOM: Final[Tuple[int, int, int]] = (0x15, 0x59, 0xC0)
"""渐变结束色 #1559c0。"""

CORNER_RADIUS: Final[float] = 14.0
"""圆角方块半径（64 坐标系）。maskable 版不使用（背景铺满）。"""

BOOK_BOX: Final[Tuple[float, float, float, float]] = (14.0, 9.0, 36.0, 46.0)
"""白色书页 ``(x, y, w, h)``。"""

BOOK_RADIUS: Final[float] = 5.5
"""书页圆角半径。"""

CHECK_POINTS: Final[Tuple[Tuple[float, float], ...]] = (
    (21.0, 31.5),
    (28.5, 39.5),
    (43.0, 22.0),
)
"""对勾折线顶点。"""

CHECK_WIDTH: Final[float] = 7.6
"""对勾线宽。"""

CHECK_COLOR: Final[Tuple[int, int, int]] = GRADIENT_TOP
"""对勾颜色与渐变起始色相同（视觉上是「蓝勾白页」）。"""

MASKABLE_CONTENT_SCALE: Final[float] = 0.6
"""maskable 版图形元素相对画布的缩放比例。

W3C maskable 规范的安全区是**内切 80% 圆**（半径 = 画布 40%）。缩到 60% 后
书页外接圆半径 = ``hypot(36*0.6/2, 46*0.6/2) / 64 ≈ 0.274`` 画布宽，远小于
0.40，因此圆形/方圆/水滴形裁切都不会切到对勾。
"""

OUTPUT_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "frontend" / "public" / "icons"
"""图标输出目录。"""

ICON_SIZES: Final[Tuple[int, ...]] = (192, 512)
"""需要产出的尺寸（PWA 安装的最低要求 + 高清屏）。"""


def build_gradient(size: int) -> Image.Image:
    """构造垂直线性渐变。

    用「1×N 的条带再横向拉伸」实现：逐行算一次插值，避免 N×N 次 putpixel。

    Args:
        size: 输出正方形边长（像素）。

    Returns:
        边长为 ``size`` 的 RGB 图像，第 0 行为 ``GRADIENT_TOP``，
        最后一行为 ``GRADIENT_BOTTOM``。
    """
    strip = Image.new("RGB", (1, size))
    span = max(size - 1, 1)
    for y in range(size):
        ratio = y / span
        strip.putpixel(
            (0, y),
            tuple(
                round(top + (bottom - top) * ratio)
                for top, bottom in zip(GRADIENT_TOP, GRADIENT_BOTTOM)
            ),
        )
    # NEAREST：横向复制同一行像素，不做任何混色
    return strip.resize((size, size), Image.NEAREST)


def build_background(size: int, *, maskable: bool) -> Image.Image:
    """构造图标背景。

    Args:
        size: 画布边长（像素，已是超采样后的尺寸）。
        maskable: True 时背景铺满整块且不透明（形状交给系统裁切）；
            False 时按 ``CORNER_RADIUS`` 做圆角，四角透明。

    Returns:
        RGBA 图像。
    """
    gradient = build_gradient(size).convert("RGBA")
    if maskable:
        return gradient

    unit = size / VIEWBOX
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0.0, 0.0, float(size - 1), float(size - 1)),
        radius=CORNER_RADIUS * unit,
        fill=255,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)
    return canvas


def draw_foreground(image: Image.Image, size: int, *, maskable: bool) -> None:
    """在背景上绘制书页与对勾（就地修改）。

    Args:
        image: 已铺好背景的 RGBA 画布。
        size: 画布边长（像素，已是超采样后的尺寸）。
        maskable: True 时把图形整体缩到中心 60% 区域。
    """
    unit = size / VIEWBOX
    scale = MASKABLE_CONTENT_SCALE if maskable else 1.0

    def project(value: float) -> float:
        """把设计坐标换算成像素坐标（maskable 时围绕画布中心缩放）。"""
        return size / 2.0 + (value - VIEWBOX / 2.0) * unit * scale

    draw = ImageDraw.Draw(image)

    # 白色书页
    left, top, width, height = BOOK_BOX
    draw.rounded_rectangle(
        (
            project(left),
            project(top),
            project(left + width),
            project(top + height),
        ),
        radius=BOOK_RADIUS * unit * scale,
        fill=(255, 255, 255, 255),
    )

    # 蓝色对勾：joint="curve" 让折点圆滑，两端另行补圆点做 stroke-linecap="round"
    points = [(project(x), project(y)) for x, y in CHECK_POINTS]
    stroke = CHECK_WIDTH * unit * scale
    draw.line(points, fill=(*CHECK_COLOR, 255), width=round(stroke), joint="curve")
    for x, y in points:
        draw.ellipse(
            (x - stroke / 2.0, y - stroke / 2.0, x + stroke / 2.0, y + stroke / 2.0),
            fill=(*CHECK_COLOR, 255),
        )


def render_icon(target: int, *, maskable: bool) -> Image.Image:
    """渲染单个图标。

    Args:
        target: 目标边长（192 / 512）。
        maskable: 是否生成 maskable 版本。

    Returns:
        边长为 ``target`` 的 RGBA 图像。
    """
    size = target * SUPERSAMPLE
    canvas = build_background(size, maskable=maskable)
    draw_foreground(canvas, size, maskable=maskable)
    # RGBa 是预乘 alpha 模式：直接缩放 RGBA 会让透明像素的黑色渗进边缘形成描边
    return canvas.convert("RGBa").resize((target, target), Image.LANCZOS).convert("RGBA")


def main() -> int:
    """生成全部图标并打印尺寸断言结果。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for target in ICON_SIZES:
        for maskable in (False, True):
            suffix = "-maskable" if maskable else ""
            path = OUTPUT_DIR / f"icon-{target}{suffix}.png"
            icon = render_icon(target, maskable=maskable)
            icon.save(path, format="PNG", optimize=True)

            # 读回校验：不能只看文件名对不对
            with Image.open(path) as saved:
                assert saved.size == (target, target), f"{path.name} 尺寸异常：{saved.size}"
                assert saved.mode == "RGBA", f"{path.name} 通道异常：{saved.mode}"
            print(f"{path.relative_to(OUTPUT_DIR.parents[2])}  {saved.size}  {saved.mode}  {path.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
