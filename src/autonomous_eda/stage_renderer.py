"""Tool programs used by the process-isolated physical snapshot exporter."""

OPENROAD = r"""
proc rect {box} {
    return [list [$box xMin] [$box yMin] [$box xMax] [$box yMax]]
}
proc row {stream args} {
    set fields {}
    foreach value $args {
        lappend fields [binary encode hex [encoding convertto utf-8 $value]]
    }
    puts $stream [join $fields \t]
}
if {$::env(AEDA_FORMAT) eq "def"} {
    foreach lef [lsort [glob /inputs/*.lef]] { read_lef $lef }
    read_def /inputs/source.def
} else {
    read_db /inputs/source.odb
}
set block [ord::get_db_block]
if {$block eq "NULL"} { error "No OpenDB block" }
set die [rect [$block getDieArea]]
set dbu [$block getDbUnitsPerMicron]
set area {}
foreach value $die delta {-2 -2 2 2} {
    lappend area [expr {double($value) / $dbu + $delta}]
}
set stream [open /work/geometry.tsv w]
row $stream design [$block getName] $dbu {*}$die
foreach inst [$block getInsts] {
    if {![$inst isPlaced]} { continue }
    set master [$inst getMaster]
    row $stream cell [$inst getName] [$master getName] [$master getType] {*}[rect [$inst getBBox]]
}
set iterator [odb::dbWirePathItr]
set wire_path [odb::dbWirePath]
set wire_node [odb::dbWirePathShape]
foreach net [$block getNets] {
    set name [$net getName]
    set wire [$net getWire]
    row $stream net $name [$net getSigType] [expr {$wire ne "NULL"}]
    if {$wire ne "NULL"} {
        $iterator begin $wire
        while {[$iterator getNextPath $wire_path]} {
            while {[$iterator getNextShape $wire_node]} {
                set shape [$wire_node cget -shape]
                set layer [$shape getTechLayer]
                if {$layer ne "NULL" && ![$shape isVia]} {
                    row $stream wire $name [$layer getName] {*}[rect $shape]
                }
            }
        }
    }
    foreach pin [$net getITerms] {
        set inst [$pin getInst]
        set term [$pin getMTerm]
        set location {}
        if {[$inst isPlaced]} {
            set box [rect [$pin getBBox]]
            lassign $box x1 y1 x2 y2
            if {$x1 <= $x2 && $y1 <= $y2} {
                set location [list [expr {($x1 + $x2) / 2.0}] [expr {($y1 + $y2) / 2.0}]]
            }
        }
        row $stream terminal $name [$inst getName] [$term getName] \
            [$term getIoType] [$term getSigType] {*}$location
    }
    foreach pin [$net getBTerms] {
        row $stream port $name [$pin getName] [$pin getIoType]
        foreach shape [$pin getBPins] {
            if {[$shape getPlacementStatus] ni {PLACED FIRM FIXED COVER}} { continue }
            foreach box [$shape getBoxes] {
                row $stream pin [$pin getName] $name [[$box getTechLayer] getName] {*}[rect $box]
            }
        }
    }
    if {![catch {$net getGuides} guides]} {
        foreach guide $guides {
            row $stream guide $name [[$guide getLayer] getName] {*}[rect [$guide getBox]]
        }
    }
}
set grid [$block getGCellGrid]
if {$grid ne "NULL"} {
    set xs [$grid getGridX]
    set ys [$grid getGridY]
    foreach layer [[ord::get_db_tech] getLayers] {
        if {[$layer getRoutingLevel] == 0} { continue }
        for {set i 0} {$i < [llength $xs] - 1} {incr i} {
            for {set j 0} {$j < [llength $ys] - 1} {incr j} {
                set cap [$grid getCapacity $layer $i $j]
                set use [$grid getUsage $layer $i $j]
                if {$cap <= 0 && $use <= 0} { continue }
                row $stream congestion [$layer getName] $use $cap \
                    [lindex $xs $i] [lindex $ys $j] \
                    [lindex $xs [expr {$i + 1}]] [lindex $ys [expr {$j + 1}]]
            }
        }
    }
}
close $stream
save_image -width $::env(AEDA_WIDTH) -area $area /work/layout.png
puts "AEDA_STAGE_OK"
"""

KLAYOUT = r"""
include RBA
layout = Layout.new
layout.read("/inputs/source.gds")
top = layout.top_cells.find { |cell| cell.name == $top } if $top && !$top.empty?
top ||= layout.top_cells.first if (!$top || $top.empty?) && layout.top_cells.length == 1
raise "Missing, ambiguous or empty GDS top cell" unless top && !top.bbox.empty?
def row(file, *fields)
  file.puts fields.map { |value| value.to_s.encode("UTF-8").unpack1("H*") }.join("\t")
end
File.open("/work/geometry.tsv", "w") do |file|
  box = top.bbox
  row(file, "design", top.name, 1 / layout.dbu, box.left, box.bottom, box.right, box.top)
  count = 0
  layout.layer_indices.each do |index|
    info = layout.get_info(index)
    layer = "#{info.layer}/#{info.datatype}"
    iterator = top.begin_shapes_rec(index)
    until iterator.at_end?
      break if count >= 300000
      shape = iterator.shape
      polygon = shape.polygon if shape.is_box? || shape.is_polygon? || shape.is_path?
      if polygon
        poly = polygon.transformed(iterator.itrans)
        rings = [poly.each_point_hull.map { |p| "#{p.x},#{p.y}" }.join(" ")]
        poly.holes.times do |hole|
          rings << poly.each_point_hole(hole).map { |p| "#{p.x},#{p.y}" }.join(" ")
        end
        row(file, "polygon", layer, *rings)
        count += 1
      end
      polygon = nil
      iterator.next
    end
    if count >= 300000
      row(file, "limitation", "GDS vector preview capped at 300000 polygons. See checkpoint PNG.")
      break
    end
  end
end
v = LayoutView.new
v.set_config("background-color", "#0b1220")
v.set_config("grid-visible", "false")
v.load_layout("/inputs/source.gds", 0)
v.select_cell(top.cell_index, 0)
v.load_layer_props($layer_map) if $layer_map && !$layer_map.empty?
v.max_hier
box = top.bbox.to_dtype(layout.dbu)
margin = 2.0
v.zoom_box(DBox.new(box.left - margin, box.bottom - margin, box.right + margin, box.top + margin))
height = [1, [2400, ($width.to_i * (box.height + 4) / (box.width + 4)).round].min].max
v.save_image("/work/layout.png", $width.to_i, height)
puts "AEDA_STAGE_OK #{Application.instance.version}"
"""
