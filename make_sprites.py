# -*- coding: utf-8 -*-
"""
把一套「白底三视图」做成桌宠能用的衣服。

用法：
    python make_sprites.py <三视图目录> [-o <输出目录>] [--preview]

三视图目录里要有（白底、人物居中）：
    正面.png  侧面.png  背面.png

输出（默认 sprites/clothes/<目录名>/）：
    正面.png 侧面.png 背面.png           统一高 340 的透明图
    正面_187.png … _238.png … _306.png    小 / 中 / 大三档预渲染（避免运行时缩放糊掉）
    config.json                           部位区域的模板（没有才写，已存在不动）
    zones-preview.png                     加 --preview 时输出，用来对着调部位区域

做法和上游一致：白底泛洪抠图 → 边缘白底去混合（消除白边）→ 预乘 alpha 缩放。
"""
import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("需要 Pillow：pip install pillow")

BASE_H = 340                     # 桌宠的基准高度，改这里要同步改 dspet.py 里的 340
SIZES = [187, 238, 306]          # 0.55 / 0.7 / 0.9 三档
VIEWS = ["正面", "侧面", "背面"]
HERE = os.path.dirname(os.path.abspath(__file__))

# 部位模板：最外层是数组，每个部位一个元素；数组顺序就是优先级（越靠前越优先）
ZONE_TEMPLATE = [
    {"name": "头部", "rects": [{"x": {"min": 0.01, "max": 0.98}, "y": {"min": 0.11, "max": 0.55}}]},
    {"name": "胸部", "rects": [{"x": {"min": 0.37, "max": 0.59}, "y": {"min": 0.55, "max": 0.68}}]},
    {"name": "手臂", "rects": [
        {"x": {"min": 0.20, "max": 0.37}, "y": {"min": 0.59, "max": 0.77}},
        {"x": {"min": 0.59, "max": 0.75}, "y": {"min": 0.59, "max": 0.77}},
    ]},
    {"name": "腿脚", "rects": [{"x": {"min": 0.34, "max": 0.62}, "y": {"min": 0.88, "max": 0.99}}]},
    {"name": "尾巴", "rects": [{"x": {"min": 0.75, "max": 0.95}, "y": {"min": 0.69, "max": 0.85}}]},
    {"name": "肚子", "rects": [{"x": {"min": 0.34, "max": 0.62}, "y": {"min": 0.68, "max": 0.88}}]},
]
PREVIEW_COLORS = [(255, 99, 99), (255, 190, 80), (120, 220, 140),
                  (110, 170, 255), (200, 130, 255), (90, 230, 230),
                  (255, 230, 120), (150, 255, 200)]


def cutout(path):
    """白底 → 透明：从四角泛洪，人物内部的白不受影响；再削掉贴着透明区的白边。"""
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        ImageDraw.floodfill(im, (sx, sy), (0, 0, 0, 0), thresh=30)
    for _ in range(3):
        px = im.load()
        changed = False
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a == 0 or not (r > 215 and g > 215 and b > 215):
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                        px[x, y] = (0, 0, 0, 0)
                        changed = True
                        break
        if not changed:
            break
    bbox = im.getbbox()
    if bbox is None:
        raise RuntimeError(os.path.basename(path) + "：抠图后什么都没有，是不是背景不是白的？")
    im = im.crop(bbox)
    w2, h2 = im.size
    scale = BASE_H / h2
    return im.resize((max(1, round(w2 * scale)), BASE_H), Image.LANCZOS)


def decontaminate(im):
    """边缘像素对白底去混合：pixel = fg*a + 255*(1-a) → fg = (pixel - 255*(1-a)) / a"""
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not (0 < a < 255):
                continue
            if a < 40:            # 极淡的边缘直接丢掉，不然除噪会放大成脏点
                px[x, y] = (0, 0, 0, 0)
                continue
            t = a / 255.0
            px[x, y] = (int(max(0, min(255, (r - 255 * (1 - t)) / t))),
                        int(max(0, min(255, (g - 255 * (1 - t)) / t))),
                        int(max(0, min(255, (b - 255 * (1 - t)) / t))), a)
    return im


def premult_resize(im, height):
    """预乘 alpha 缩放：黑底、白底各缩一次，再解出真实颜色和覆盖度。"""
    w0, h0 = im.size
    nw = max(1, round(w0 * height / h0))
    black = Image.alpha_composite(Image.new("RGBA", im.size, (0, 0, 0, 255)), im).resize((nw, height), Image.LANCZOS)
    white = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im).resize((nw, height), Image.LANCZOS)
    out = Image.new("RGBA", (nw, height))
    bp, wp, op = black.load(), white.load(), out.load()
    for y in range(height):
        for x in range(nw):
            br, bg, bb, _ = bp[x, y]
            wr, wg, wb, _ = wp[x, y]
            a = 255 - max(wr - br, wg - bg, wb - bb)
            if a < 6:
                continue
            t = a / 255.0
            op[x, y] = (int(max(0, min(255, br / t))), int(max(0, min(255, bg / t))),
                        int(max(0, min(255, bb / t))), a)
    return out


def draw_preview(out_dir):
    """在正面图上把部位区域画出来（读这套衣服的 config.json），方便对着调比例。"""
    src = os.path.join(out_dir, "正面.png")
    if not os.path.exists(src):
        return None
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    canvas = Image.new("RGBA", (w, h), (18, 24, 36, 255))
    canvas.alpha_composite(im)
    d = ImageDraw.Draw(canvas)
    zones = ZONE_TEMPLATE
    try:
        with open(os.path.join(out_dir, "config.json"), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, list) and loaded:
            zones = loaded
    except Exception:
        pass
    for i, entry in enumerate(zones):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        rects = entry.get("rects") or []
        if isinstance(rects, dict):
            rects = [rects]
        c = PREVIEW_COLORS[i % len(PREVIEW_COLORS)]
        for b in rects:
            try:
                x0, x1 = b["x"]["min"] * w, b["x"]["max"] * w
                y0, y1 = b["y"]["min"] * h, b["y"]["max"] * h
            except Exception:
                continue
            d.rectangle([x0, y0, x1, y1], outline=c, width=2)
            d.text((x0 + 3, max(0, y0 + 2)), name, fill=c)
    path = os.path.join(out_dir, "zones-preview.png")
    canvas.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description="把白底三视图做成一套桌宠衣服")
    ap.add_argument("src", help="三视图目录（里面是 正面.png / 侧面.png / 背面.png）")
    ap.add_argument("-o", "--out", help="输出目录，默认 sprites/clothes/<目录名>")
    ap.add_argument("--preview", action="store_true", help="额外画一张部位区域示意图")
    ap.add_argument("--icon", action="store_true", help="顺便把 64px 托盘图标写到 sprites/icon.png")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    name = os.path.basename(src.rstrip("\\/"))
    out = os.path.abspath(args.out) if args.out else os.path.join(HERE, "sprites", "clothes", name)
    os.makedirs(out, exist_ok=True)

    missing = [v for v in VIEWS if not os.path.exists(os.path.join(src, v + ".png"))]
    if missing:
        sys.exit("缺这几张：" + "、".join(m + ".png" for m in missing))

    for view in VIEWS:
        base = decontaminate(cutout(os.path.join(src, view + ".png")))
        base.save(os.path.join(out, view + ".png"))
        for h in SIZES:
            premult_resize(base, h).save(os.path.join(out, "%s_%d.png" % (view, h)))
            print("%s_%d.png" % (view, h))
        print("%s.png %s" % (view, base.size))

    cfg = os.path.join(out, "config.json")
    if not os.path.exists(cfg):
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(ZONE_TEMPLATE, f, ensure_ascii=False, indent=2)
        print("写了部位模板：" + cfg + "（记得按自己的图改比例）")
        print("标部位推荐用配套网页工具 image-part-annotator："
              "https://github.com/088uiop/image-part-annotator")
        print("用它打开刚生成的 正面.png 框选部位（名字随你起），导出的 config.json 直接放进本目录；")
        print("数组顺序就是优先级，越靠上的部位在重叠时越优先。")

    if args.icon:
        base = Image.open(os.path.join(out, "正面.png")).convert("RGBA")
        premult_resize(base, 64).save(os.path.join(HERE, "sprites", "icon.png"))
        print("图标 -> sprites/icon.png")

    if args.preview:
        p = draw_preview(out)
        if p:
            print("部位示意 -> " + p)

    print("完成：" + out)


if __name__ == "__main__":
    main()
