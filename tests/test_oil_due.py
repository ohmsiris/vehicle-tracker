from app import history


def test_a_newer_oil_change_with_no_written_figure_counts_from_its_own_odometer():
    status = {"v56": {"next_service_mileage": None, "last_oil_mileage": 177921},          # 19/07/2026, nothing written on the bill
              "v1": {"next_service_mileage": 680000, "last_oil_mileage": 670000},          # written on the bill: kept
              "v9": {"next_service_mileage": None, "last_oil_mileage": None}}              # no oil change known: nothing
    history.fill_oil_due(status, {"v56": 10000, "v1": 10000, "v9": 20000})
    assert status["v56"]["next_service_mileage"] == 187921 and status["v56"]["next_service_from_plan"] is True
    assert status["v1"]["next_service_mileage"] == 680000 and not status["v1"].get("next_service_from_plan")
    assert status["v9"]["next_service_mileage"] is None
