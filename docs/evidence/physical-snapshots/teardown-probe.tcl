set results /inputs/results/sky130hd/gcd/base
foreach {stage checkpoint} {
    floorplan 2_floorplan.odb
    place 3_place.odb
    cts 4_cts.odb
    grt 5_1_grt.odb
    route 5_route.odb
    finish 6_final.odb
} {
    if {$stage ne "floorplan"} {
        odb::dbBlock_destroy [ord::get_db_block]
        odb::dbChip_destroy [[ord::get_db] getChip]
    }
    read_db $results/$checkpoint
    set block [ord::get_db_block]
    set die [$block getDieArea]
    set placed 0
    foreach instance [$block getInsts] {
        if {[$instance isPlaced]} { incr placed }
    }
    set wired 0
    foreach net [$block getNets] {
        if {[$net getWire] ne "NULL"} { incr wired }
    }
    puts [join [list AEDA_STAGE $stage [$block getName] [$block getDbUnitsPerMicron] \
        [$die xMin] [$die yMin] [$die xMax] [$die yMax] [llength [$block getRows]] \
        [llength [$block getInsts]] $placed [llength [$block getNets]] $wired] "\t"]
}
