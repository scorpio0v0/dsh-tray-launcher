# -*- coding: utf-8 -*-
"""生成托盘/快捷方式用的自有图标 icon.ico（本项目原创图案，非 DeepSeek 商标）。

用法：
    python make_icon.py [输出路径]

默认输出到脚本同目录的 icon.ico。需要已安装 PyQt5。
"""
import os
import struct
import sys

from PyQt5.QtCore import QBuffer, QByteArray, QRectF, Qt
from PyQt5.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPen, QPixmap

HERE = os.path.dirname(os.path.abspath(__file__))


def draw_pixmap(size: int) -> QPixmap:
    """画一个深色圆角方块 + 蓝色光条 + 白色字母 D 的原创图案。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 圆角底板：深灰蓝
    radius = max(2, size // 5)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#1e2430"))
    painter.drawRoundedRect(0, 0, size, size, radius, radius)

    # 内部蓝色渐变光条（终端指示条）
    bar_height = max(2.0, size / 12.0)
    gradient = QLinearGradient(size * 0.2, 0, size * 0.8, 0)
    gradient.setColorAt(0.0, QColor("#1f6feb"))
    gradient.setColorAt(1.0, QColor("#58a6ff"))
    painter.setBrush(gradient)
    painter.drawRoundedRect(
        QRectF(size * 0.2, size * 0.2, size * 0.6, bar_height),
        bar_height / 2,
        bar_height / 2,
    )

    # 主体字母 D：竖条 + 右半圆弧（纯几何绘制，不依赖系统字体）
    pen = QPen(QColor("#e6edf3"))
    pen.setWidthF(size * 0.14)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(QColor("#e6edf3"))
    painter.drawRoundedRect(
        QRectF(size * 0.22, size * 0.30, size * 0.15, size * 0.44),
        size * 0.07, size * 0.07,
    )
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(
        QRectF(size * 0.22, size * 0.30, size * 0.62, size * 0.44),
        90 * 16, -180 * 16,
    )
    painter.end()
    return pixmap


def pixmap_to_png(pixmap: QPixmap) -> bytes:
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(array)


def write_ico(png: bytes, path: str) -> None:
    """把一份 256px PNG 包进 ICO 容器（Vista+ 支持内嵌 PNG 的 ICO 条目）。

    结构：ICONDIR(6) + ICONDIRENTRY(16) + PNG 数据。
    """
    assert len(png) < 0x7FFF00, "PNG 太大，无法放入单条目 ICO"
    header = struct.pack(
        "<HHHHHHIII",
        0,        # reserved
        1,        # type: icon
        1,        # 条目数
        0,        # width:  0 表示 256
        0,        # height: 0 表示 256
        0,        # 调色板数
        1,        # 色彩平面
        32,       # 位深
        len(png),  # 数据大小
        22,        # 数据偏移（6 + 16）
    )
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(png)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "icon.ico")
    # QPixmap 需要 GUI 实例；offscreen 平台保证无窗口也能运行
    app = QGuiApplication(["dsh-tray-launcher", "-platform", "offscreen"])
    pixmap = draw_pixmap(256)

    if pixmap.save(out, "ICO"):
        print(f"已生成图标（Qt ICO）：{out}")
        return 0

    # Qt 的 ICO 写入不可用时，手工打包 PNG 进 ICO 容器
    png = pixmap_to_png(pixmap)
    write_ico(png, out)
    print(f"已生成图标（PNG-in-ICO）：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
