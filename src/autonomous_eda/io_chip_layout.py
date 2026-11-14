"""KLayout library inventory, ORFS stream-out, and independent GDS checks."""

from __future__ import annotations

from typing import Any

from .io_ring import FLOW

KLAYOUT = "/usr/bin/klayout"
CORE_GDS = f"{FLOW}/platforms/sky130hd/gds/sky130_fd_sc_hd.gds"
TECH = f"{FLOW}/platforms/sky130hd/sky130hd.lyt"
STREAM = f"{FLOW}/util/def2stream.py"

LAYOUT_SCRIPT = r"""
require 'json'
include RBA
spec = JSON.parse(File.read('/work/layout.json'))
views = spec.fetch('views')
if spec.fetch('mode') == 'library'
  seen = Hash.new(0)
  views.each do |file|
    layout = Layout.new
    layout.read(file)
    spec.fetch('masters').each_key do |name|
      cell = layout.cell(name)
      seen[name] += 1 if cell && !cell.is_empty?
    end
  end
  File.write('/work/layout-result.json', JSON.generate({ 'master_views' => seen }))
else
  layout = Layout.new
  layout.read(views.fetch(0))
  top = layout.cell(spec.fetch('top'))
  counts = Hash.new(0)
  empty = []
  chips = []
  polygon_shapes = 0
  if top
    top.each_inst { |inst| counts[inst.cell.name] += inst.size }
    reachable = [top.cell_index] + top.called_cells.to_a
    empty = reachable.map { |i| layout.cell(i) }.select(&:is_empty?).map(&:name).sort
    index = layout.find_layer(235, 4)
    if index
      top.shapes(index).each { |shape| chips << shape.bbox.to_s if shape.is_box? }
    end
    layout.layer_indices.each do |layer|
      next if layer == index
      top.shapes(layer).each do |shape|
        polygon_shapes += 1 if shape.is_polygon? || shape.is_path? || shape.is_box?
      end
    end
    expected = Box.new(0, 0, *spec.fetch('size_um').map { |value| (value / layout.dbu).round })
    boundary = chips == [expected.to_s]
    extent = expected.contains?(top.bbox.p1) && expected.contains?(top.bbox.p2)
  end
  File.write('/work/layout-result.json', JSON.generate({
    'top' => top&.name, 'top_levels' => layout.top_cells.map(&:name).sort,
    'masters' => counts, 'empty_references' => empty, 'boundary' => boundary || false,
    'extent_inside_die' => extent || false, 'boundary_boxes' => chips,
    'geometry_bbox_dbu' => top&.bbox&.to_s,
    'top_geometry_shapes' => polygon_shapes, 'hierarchical_cells' => layout.cells,
    'database_unit_um' => layout.dbu
  }))
end
puts "AEDA_LAYOUT_COMPLETE"
"""


def gds_matches(result: dict[str, Any], masters: dict[str, int]) -> bool:
    return all(result["master_views"].get(name) == 1 for name in masters)


def reopened(result: dict[str, Any], top: str, masters: dict[str, int]) -> bool:
    return bool(
        result["top"] == top
        and result["top_levels"] == [top]
        and not result["empty_references"]
        and result["boundary"]
        and result["top_geometry_shapes"] > 0
        and result["hierarchical_cells"] > 1
        and {
            name: count for name, count in result["masters"].items() if not name.startswith("VIA_")
        }
        == masters
    )


def stream_args(top: str, views: list[str]) -> list[str]:
    return [
        "-b",
        "-r",
        STREAM,
        "-rd",
        "tech_file=/work/technology.lyt",
        "-rd",
        "layer_map=",
        "-rd",
        "in_def=/work/routed.def",
        "-rd",
        f"design_name={top}",
        "-rd",
        f"in_files={' '.join([CORE_GDS, *views])}",
        "-rd",
        "seal_file=",
        "-rd",
        "out_file=/work/chip.gds",
    ]
