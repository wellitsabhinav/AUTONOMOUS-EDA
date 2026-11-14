"""Export views of verified Sky130HD GDS through the pinned KLayout container."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from .orfs import preflight
from .reference import IMAGE

LAYER_MAP = "/OpenROAD-flow-scripts/flow/platforms/sky130hd/sky130hd.lyp"
VIEWS = ("layout_overview", "layout_placement", "layout_routing", "layout_detail")
ROUTING = [67, 68, 69, 70, 71, 72]
SCRIPT = """
include RBA
v = LayoutView.new
v.set_config("background-color", "#0b1220")
v.set_config("grid-visible", "false")
v.load_layout("/work/source.gds", 0)
v.load_layer_props($layer_map)
cv = v.active_cellview
raise "Unexpected layout top" unless cv.cell.name == $top
v.max_hier
bbox = cv.cell.bbox.to_dtype(cv.layout.dbu)
raise "Empty layout" if bbox.empty?
def visibility(v, layers, boundary)
  it = v.begin_layers
  until it.at_end?
    node = it.current
    node.visible = layers.include?(node.source_layer) ||
      (boundary && node.source_layer == 235 && node.source_datatype == 4)
    v.set_layer_properties(it, node)
    it.next
  end
end
v.zoom_fit
v.save_image("/work/layout_overview.png", 1600, 1400)
visibility(v, [67,68,69,70,71,72], true)
v.save_image("/work/layout_routing.png", 1600, 1400)
v.zoom_box(DBox.new(bbox.center.x - bbox.width / 8, bbox.center.y - bbox.height / 8,
  bbox.center.x + bbox.width / 8, bbox.center.y + bbox.height / 8))
v.save_image("/work/layout_detail.png", 1600, 1400)
boxes = Layout.new
boxes.dbu = cv.layout.dbu
outline = boxes.create_cell("PLACEMENT")
cells = boxes.layer(236, 0)
count = 0
def placed(cell, transform, output, depth)
  raise "Layout hierarchy is too deep" if depth > 16
  count = 0
  cell.each_inst do |inst|
    child = inst.cell
    next if child.name =~ /__(fill|decap|tapvpwr)/
    inst.cell_inst.each_cplx_trans do |position|
      absolute = transform * position
      if child.name.start_with?("sky130_fd_sc_hd__")
        output.insert(absolute * child.bbox)
        count += 1
      else
        count += placed(child, absolute, output, depth + 1)
      end
    end
  end
  count
end
count = placed(cv.cell, ICplxTrans.new, outline.shapes(cells), 0)
raise "No standard-cell placements" if count == 0
boxes.write("/work/placement-preview.gds")
placement = LayoutView.new
placement.set_config("background-color", "#0b1220")
placement.set_config("grid-visible", "false")
placement.load_layout("/work/placement-preview.gds", 0)
placement.max_hier
it = placement.begin_layers
until it.at_end?
  node = it.current
  node.visible = true
  node.frame_color = 0x8de1e6
  node.fill_color = 0x245a77
  node.dither_pattern = 0
  placement.set_layer_properties(it, node)
  it.next
end
placement.zoom_box(bbox)
placement.save_image("/work/layout_placement.png", 1600, 1400)
puts "AEDA_LAYOUT #{Application.instance.version} #{bbox.width} #{bbox.height}"
"""


def render_layout(work: Path, top: str) -> tuple[str, dict[str, object]]:
    if sys.platform != "linux":
        raise ValueError("KLayout export requires the cached Linux ORFS image")
    docker = preflight()
    if any(char in str(work) for char in ',"\r\n'):
        raise ValueError("Invalid layout staging directory")
    (work / "render.rb").write_text(SCRIPT, encoding="utf-8")
    name = f"aeda-{uuid4().hex}"
    command = [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--name",
        name,
        "--memory",
        "768m",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cpus",
        "1",
        "-e",
        "QT_QPA_PLATFORM=offscreen",
        "--mount",
        f"type=bind,source={work},target=/work",
        "--entrypoint",
        "klayout",
        IMAGE,
        "-zz",
        "-r",
        "/work/render.rb",
        "-rd",
        f"layer_map={LAYER_MAP}",
        "-rd",
        f"top={top}",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=15, check=False)
        raise
    summary = next(
        (line.split() for line in result.stdout.splitlines() if line.startswith("AEDA_LAYOUT ")), []
    )
    if result.returncode or len(summary) != 5 or summary[1] != "KLayout":
        raise ValueError("KLayout offscreen export failed")
    return summary[2], {
        "container": IMAGE,
        "layer_mapping": LAYER_MAP,
        "routing_layer_numbers": ROUTING,
        "placement_layer": "GDS instance bounding boxes, excluding fill, decap and tap cells",
        "top": top,
        "bbox_um": [float(n) for n in summary[3:]],
        "detail": "central quarter by width and height",
        "script": json.dumps(SCRIPT),
    }
