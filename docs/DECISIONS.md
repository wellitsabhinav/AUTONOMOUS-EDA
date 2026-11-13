# Decisions

| Decision | Evidence and alternative |
| --- | --- |
| Use ORFS as the physical implementation backend | Its public Ibex/Sky130HD reference gives a reproducible starting point. Independent orchestration can reuse upstream algorithms and artifacts. |
| Use native OpenTitan metadata for source resolution | FuseSoC and TopGen describe dependencies and generated inputs that a directory glob cannot resolve. |
| Defer the project license | The owner requested approval before selecting a license. |
