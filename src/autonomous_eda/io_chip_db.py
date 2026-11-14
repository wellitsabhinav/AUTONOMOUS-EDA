"""Fixed OpenROAD probes for library abstracts, signal association and routing."""

from __future__ import annotations

from typing import Any

from .io_chip_config import CONTROL_PINS, SIGNAL_PINS, ChipConfig
from .io_ring import FLOW, GPIO, inventory

ABSTRACTS = r"""
set db [ord::get_db]
set layer [[ord::get_db_tech] findLayer met2]
if {$layer eq "NULL" || [$layer getRoutingLevel] <= 0} { error "missing met2" }
set out [open /work/abstracts.tsv w]
foreach {name class width height} $required_masters {
  set master [$db findMaster $name]
  if {$master eq "NULL"} { error "missing LEF master $name" }
  if {[$master getType] ne $class || [$master getWidth] != $width ||
      [$master getHeight] != $height} { error "unexpected LEF class or size: $name" }
  puts $out "master\t$name\t$class\t$width\t$height"
}
foreach {master pin direction use shape_layer} $required_pins {
  set mterm [[$db findMaster $master] findMTerm $pin]
  if {$mterm eq "NULL" || [$mterm getIoType] ne $direction ||
      [$mterm getSigType] ne $use} { error "missing or incompatible LEF pin $master/$pin" }
  set found 0
  foreach mpin [$mterm getMPins] {
    foreach box [$mpin getGeometry] {
      if {[[$box getTechLayer] getName] eq $shape_layer &&
          [$box xMax] > [$box xMin] && [$box yMax] > [$box yMin]} { set found 1 }
    }
  }
  if {$shape_layer ne "-" && !$found} { error "missing $shape_layer access: $master/$pin" }
  puts $out "pin\t$master\t$pin\t$direction\t$use\t$shape_layer"
}
close $out
puts AEDA_ABSTRACTS_COMPLETE
"""


def abstract_script(placement: dict[str, Any]) -> str:
    masters = [
        f"{GPIO} PAD_INOUT 80000 210965",
        f"{placement['corner']} ENDCAP_TOPRIGHT 200000 204000",
    ]
    masters.extend(
        f"{name} PAD_SPACER {int(name.rsplit('_', 1)[1][:-2]) * 1000} 197965"
        for name in placement["fillers"]
    )
    pins = [
        f"{GPIO} IN OUTPUT SIGNAL met2",
        f"{GPIO} OUT INPUT SIGNAL met2",
        f"{GPIO} PAD INOUT SIGNAL met5",
    ]
    pins.extend(f"{GPIO} {{{pin}}} INPUT SIGNAL -" for pin in CONTROL_PINS)
    for kind, pin, use in (("power", "VCCD", "POWER"), ("ground", "VSSD", "GROUND")):
        master = next(pad["master"] for pad in placement["pads"] if pad["kind"] == kind)
        masters.append(f"{master} PAD_POWER 75000 197965")
        pins.extend(f"{cell} {pin} INOUT {use} -" for cell in (master, GPIO))
    reads = [
        f"read_lef {FLOW}/{name}"
        for name in inventory(placement)
        if name.endswith((".lef", ".tlef"))
    ]
    return "\n".join(
        [
            *reads,
            "set required_masters {" + "\n".join(masters) + "}",
            "set required_pins {" + "\n".join(pins) + "}",
            ABSTRACTS,
        ]
    )


def checked_mapping(config: ChipConfig, placement: dict[str, Any]) -> list[tuple[str, ...]]:
    signals = {p["name"]: p for p in placement["pads"] if p["kind"] in SIGNAL_PINS}
    ports = {pad["port"] for pad in signals.values()}
    if set(signals) != {entry.pad for entry in config.mappings}:
        raise ValueError("provide exactly one mapping for each signal pad in placement")
    result: list[tuple[str, ...]] = []
    for entry in config.mappings:
        pad = signals[entry.pad]
        if entry.core_pin != SIGNAL_PINS[pad["kind"]]:
            raise ValueError(f"{entry.pad}: expected core-facing pin {SIGNAL_PINS[pad['kind']]}")
        if entry.external_port in ports - {pad["port"]}:
            raise ValueError(f"{entry.pad}: external port collides with another core port")
        result.append(
            (
                entry.pad,
                pad["port"],
                pad["kind"].upper(),
                entry.core_pin,
                entry.external_port,
                pad["master"],
            )
        )
    return result


CONNECT = r"""
read_db /work/placement.odb
set block [ord::get_db_block]
puts "AEDA_ODB_DBU [$block getDbUnitsPerMicron]"
set out [open /work/connectivity.tsv w]
proc require {condition reason} {
  if {!$condition} { puts "AEDA_MAPPING_VIOLATION $reason"; error $reason }
}
set used_nets [dict create]
set ports {}
foreach bterm [$block getBTerms] { lappend ports [$bterm getName] }
set expected {}
foreach {pad port dir pin external master} $mappings { lappend expected $port }
require [expr {[lsort $ports] eq [lsort $expected]}] "unmapped or missing core ports"
foreach {pad port dir pin external master} $mappings {
  set inst [$block findInst $pad]
  require [expr {$inst ne "NULL" && [[$inst getMaster] getName] eq $master}] "wrong pad $pad"
  set iterm [$inst findITerm $pin]
  set bond [$inst findITerm PAD]
  require [expr {$iterm ne "NULL" && $bond ne "NULL"}] "missing pin on $pad"
  require [expr {[$iterm getNet] eq "NULL" && [$bond getNet] eq "NULL"}] "already wired: $pad"
  set old [$block findBTerm $port]
  require [expr {[$old getIoType] eq $dir}] "core direction mismatch: $port"
  set net [$old getNet]
  require [expr {$net ne "NULL" && [$net getSigType] eq "SIGNAL"}] "invalid signal $port"
  set name [$net getName]
  require [expr {![dict exists $used_nets $name] && [llength [$net getBTerms]] == 1}] \
    "duplicate or aliased core net: $name"
  dict set used_nets $name 1
  set sources 0
  set sinks 0
  foreach core [$net getITerms] {
    set io [[$core getMTerm] getIoType]
    if {$io eq "OUTPUT"} { incr sources }
    if {$io eq "INPUT"} { incr sinks }
    require [expr {$io in {INPUT OUTPUT}}] "unsupported core term direction on $name"
  }
  require [expr {($dir eq "INPUT" && $sources == 0 && $sinks >= 1) ||
                 ($dir eq "OUTPUT" && $sources == 1)}] "illegal driver or load count on $name"
  set extnet "aeda_external_$external"
  require [expr {[$block findNet $extnet] eq "NULL" &&
                 ($external eq $port || [$block findBTerm $external] eq "NULL")}] \
    "external name collision: $external"
  odb::dbBTerm_destroy $old
  $iterm connect $net
  set ext [odb::dbNet_create $block $extnet]
  $bond connect $ext
  set top [odb::dbBTerm_create $ext $external]
  $top setIoType $dir
  # Copy the transformed bond pin rectangle onto the external top-level terminal.
  set bpin [odb::dbBPin_create $top]
  $bpin setPlacementStatus FIRM
  set shapes 0
  foreach geometry [$bond getGeometries] {
    lassign $geometry layer rect
    odb::dbBox_create $bpin $layer [$rect xMin] [$rect yMin] [$rect xMax] [$rect yMax]
    incr shapes
  }
  require [expr {$shapes > 0}] "missing bond pin geometry on $pad"
  puts $out "signal\t$pad\t$pin\t$name\t$external\t$sources\t$sinks"
}
foreach inst [$block getInsts] {
  set pad [[$inst getMaster] getType]
  if {$pad ni {PAD_INOUT PAD_POWER}} { continue }
  foreach iterm [$inst getITerms] {
    set mterm [$iterm getMTerm]
    set name [$mterm getName]
    set use [$mterm getSigType]
    if {$name ni $controls && $use ni {POWER GROUND}} { continue }
    set net [$iterm getNet]
    set netname -
    set opposite 0
    if {$net ne "NULL"} {
      set netname [$net getName]
      foreach peer [$net getITerms] {
        set peer_use [[$peer getMTerm] getSigType]
        if {($use eq "POWER" && $peer_use eq "GROUND") ||
            ($use eq "GROUND" && $peer_use eq "POWER")} { set opposite 1; break }
      }
    }
    puts $out "prerequisite\t[$inst getName]\t$name\t$use\t$netname\t$opposite"
  }
}
close $out
write_db /work/connected.odb
write_def /work/connected.def
puts AEDA_MAPPING_COMPLETE
"""


def connect_script(mappings: list[tuple[str, ...]]) -> str:
    rows = "\n".join(" ".join(row) for row in mappings)
    return f"set mappings {{{rows}}}\nset controls {{{' '.join(CONTROL_PINS)}}}\n" + CONNECT


OBSERVE = r"""
set block [ord::get_db_block]
set out [open /work/observed.tsv w]
foreach {pad port dir pin external master} $mappings {
  set inst [$block findInst $pad]
  if {$inst eq "NULL"} { continue }
  set term [$inst findITerm $pin]
  set bond [$inst findITerm PAD]
  if {$term eq "NULL" || $bond eq "NULL"} { continue }
  set inner [$term getNet]
  set outer [$bond getNet]
  set boundary [$block findBTerm $external]
  if {$inner eq "NULL" || $outer eq "NULL" || $boundary eq "NULL"} { continue }
  set connected [expr {[[$inst getMaster] getName] eq $master &&
    $inner ne $outer && [$outer getName] eq "aeda_external_$external" &&
    [$boundary getNet] eq $outer && [$boundary getIoType] eq $dir &&
    [llength [$boundary getBPins]] == 1 && [llength [$inner getBTerms]] == 0 &&
    [llength [$inner getITerms]] >= 2 && [llength [$outer getITerms]] == 1 &&
    [llength [$outer getBTerms]] == 1}]
  set wire [$inner getWire]
  set length 0
  if {$wire ne "NULL"} { set length [$wire length] }
  puts $out "signal\t$pad\t$pin\t[$inner getName]\t$external\t$connected\t$length"
}
close $out
puts AEDA_OBSERVATION_COMPLETE
"""


def observe_script(mappings: list[tuple[str, ...]], file: str) -> str:
    rows = "\n".join(" ".join(row) for row in mappings)
    return f"read_db /work/{file}\nset mappings {{{rows}}}\n" + OBSERVE


def route_script(config: ChipConfig, mappings: list[tuple[str, ...]]) -> str:
    rows = "\n".join(" ".join(row) for row in mappings)
    return f"""read_db /work/connected.odb
set block [ord::get_db_block]
foreach {{pad port dir pin external master}} {{{rows}}} {{
  # The external terminal is a copy of the pad's existing bond geometry.
  [$block findNet aeda_external_$external] setSpecial
}}
# Abutment-only nets stay outside the signal router and the routed-signal verdict.
foreach net [$block getNets] {{
  if {{[string match *_RING [$net getName]]}} {{ $net setSpecial }}
}}
source {FLOW}/platforms/sky130hd/make_tracks.tcl
global_placement -skip_io
detailed_placement
set_routing_layers -signal {config.min_layer}-{config.max_layer}
global_route -guide_file /work/route.guide
detailed_route -output_drc /work/route-drc.rpt -droute_end_iter 12
if {{![design_is_routed]}} {{ error "design has unrouted nets" }}
puts "AEDA_ROUTE_VIOLATIONS [detailed_route_num_drvs]"
write_db /work/routed.odb
write_def /work/routed.def
puts AEDA_ROUTING_COMPLETE
"""


LIBERTY = f"""
read_liberty /work/pad.lib
set cells [get_lib_cells */{GPIO}]
if {{[llength $cells] != 1}} {{ error "missing wrapped GPIO Liberty cell" }}
set cell [lindex $cells 0]
foreach {{pin direction}} {{PAD bidirect IN output OUT input OE_N input ENABLE_H input}} {{
  set pins [get_lib_pins */{GPIO}/$pin]
  if {{[llength $pins] != 1 || [get_property [lindex $pins 0] direction] ne $direction}} {{
    error "missing or incompatible Liberty pin $pin"
  }}
}}
set arcs [$cell timing_arc_sets]
set paths {{}}
foreach arc $arcs {{
  dict set paths [list [get_name [$arc from]] [get_name [$arc to]]] 1
}}
if {{![dict exists $paths {{PAD IN}}] || ![dict exists $paths {{OUT PAD}}]}} {{
  error "wrapped GPIO lacks required PAD to IN or OUT to PAD timing arcs"
}}
puts "AEDA_GPIO_TIMING_ARC_COUNT [llength $arcs]"
puts AEDA_LIBERTY_COMPLETE
"""
