from autonomous_eda.physical_workspace import resource_pipeline


def test_incomplete_route_blocks_finish_and_retains_latest_state() -> None:
    current = [
        {"stage": "route", "state": "failed"},
        {"stage": "route", "state": "interrupted"},
        *(
            {"stage": stage, "state": "passed"}
            for stage in ("synth", "floorplan", "place", "cts", "grt")
        ),
    ]
    timeline = {item["stage"]: item for item in resource_pipeline(current)}
    assert timeline["route"]["state"] == "failed"
    assert timeline["finish"]["state"] == "blocked"
    assert timeline["finish"]["reason"] == "Waiting for route"
