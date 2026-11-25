"""Side-by-side comparison of a generated layout and its reference, same scale.

    python scripts/render_comparator_comparison.py [--sample sky130_jku_sar_adc1_adc_comp_latch]

Both layouts are rendered by KLayout zoomed to their *material* bounding box
(frame layers excluded) at one common pixels-per-micrometre, so the drawn
sizes compare directly. Reads results.json written by run_comparator_learning.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "primitive_generalization" / "comparator"


def render(gds, bbox_nm, png, px_per_um, layers=None):
    import klayout.db as db
    import klayout.lay as lay
    view = lay.LayoutView()
    view.load_layout(str(gds))
    view.max_hier()
    x0, y0, x1, y1 = (v / 1000 for v in bbox_nm)
    if layers is not None:
        it = view.begin_layers()
        while not it.at_end():
            node = it.current()
            node.visible = (node.source_layer, node.source_datatype) in layers
            it.next()
    view.zoom_box(db.DBox(x0, y0, x1, y1))
    w, h = round((x1 - x0) * px_per_um), round((y1 - y0) * px_per_um)
    view.save_image(str(png), w, h)
    return w, h


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="sky130_jku_sar_adc1_adc_comp_latch")
    ap.add_argument("--px-per-um", type=float, default=24.0)
    args = ap.parse_args(argv)
    from PIL import Image, ImageDraw, ImageFont
    res = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    s = next(x for x in res["samples"] if x["id"] == args.sample)
    ev = s["evaluation"]
    contract = json.loads((OUT / "contract.json").read_text(encoding="utf-8"))
    ref = next(x for x in contract["samples"] if x["id"] == args.sample)
    ref_gds = (OUT / ref["reference_gds"]).resolve()
    gen_gds = Path(s["generated"]["gds"])
    d = OUT / "generated" / args.sample
    k = args.px_per_um
    rb, gb = ev["reference_material_bbox_nm"], ev["generated_material_bbox_nm"]
    render(ref_gds, rb, d / "_ref.png", k)
    render(gen_gds, gb, d / "_gen.png", k)
    ref_img, gen_img = Image.open(d / "_ref.png"), Image.open(d / "_gen.png")
    try:
        font = ImageFont.truetype("msyh.ttc", 22)
        small = ImageFont.truetype("msyh.ttc", 18)
    except OSError:
        font = small = ImageFont.load_default()
    pad, head, foot = 40, 110, 70
    W = ref_img.width + gen_img.width + 3 * pad
    H = max(ref_img.height, gen_img.height) + head + foot
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(ref_img, (pad, head))
    canvas.paste(gen_img, (2 * pad + ref_img.width, head))
    dr = ImageDraw.Draw(canvas)
    um = lambda b: ((b[2] - b[0]) / 1000, (b[3] - b[1]) / 1000)
    rw, rh = um(rb)
    gw, gh = um(gb)
    dr.text((pad, 10), "答案版图（JKU 原版，含布线）", fill=(0, 0, 0), font=font)
    dr.text((pad, 42), f"材料外框 {rw:.2f} × {rh:.2f} µm = {ev['reference_material_area_nm2']/1e6:.1f} µm²",
            fill=(60, 60, 60), font=small)
    gx = 2 * pad + ref_img.width
    dr.text((gx, 10), "生成版图（仅布局）", fill=(0, 0, 0), font=font)
    dr.text((gx, 42), f"{gw:.2f} × {gh:.2f} µm = {ev['generated_area_nm2']/1e6:.1f} µm²",
            fill=(60, 60, 60), font=small)
    dr.text((gx, 68), f"面积比 {ev['ratio_to_material']:.3f}；器件层 XOR 差异 "
            f"{ev['xor_fraction_device_layers']*100:.1f}%", fill=(60, 60, 60), font=small)
    for x, img in ((pad, ref_img), (gx, gen_img)):
        dr.rectangle([x - 1, head - 1, x + img.width, head + img.height], outline=(0, 0, 0))
    y = head + max(ref_img.height, gen_img.height) + 25
    dr.line([pad, y, pad + 5 * k, y], fill=(0, 0, 0), width=4)
    dr.text((pad + 5 * k + 10, y - 12), "5 µm（两图同一比例）", fill=(0, 0, 0), font=small)
    out = d / "comparison_reference_vs_generated.png"
    canvas.save(out)
    (d / "_ref.png").unlink()
    (d / "_gen.png").unlink()
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
