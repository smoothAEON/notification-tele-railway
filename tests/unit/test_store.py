from pathlib import Path

from alert_mcp.store import AlertStore


def test_store_create_list_cancel_alert(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "alerts.db")
    created = store.create_alert(
        instrument="xauusd",
        target_price=3050.0,
        direction="above",
        note="breakout",
    )

    assert created.id == 1
    assert created.instrument == "XAU_USD"
    assert created.status == "PENDING"
    assert store.status_counts()["PENDING"] == 1

    current = store.list_current_alerts()
    assert [alert.id for alert in current] == [created.id]

    cancelled = store.cancel_alert(created.id)
    assert cancelled is not None
    assert cancelled.status == "CANCELLED"
    assert store.list_current_alerts() == []


def test_store_fire_lifecycle_and_history(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "alerts.db")
    created = store.create_alert(instrument="EUR_USD", target_price=1.1, direction="below")

    firing = store.mark_firing(created.id, trigger_price=1.0999)
    assert firing is not None
    assert firing.status == "FIRING"

    fired = store.mark_fired(created.id, trigger_price=1.0999)
    assert fired is not None
    assert fired.status == "FIRED"
    assert fired.fired_at is not None
    assert fired.trigger_price == 1.0999

    history = store.list_fired_alerts()
    assert [alert.id for alert in history] == [created.id]


def test_store_recovers_stale_firing_alerts(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "alerts.db")
    created = store.create_alert(instrument="GBP_USD", target_price=1.25, direction="above")
    assert store.mark_firing(created.id, trigger_price=1.251) is not None

    recovered = store.recover_firing_alerts()
    assert recovered == 1
    alert = store.get_alert(created.id)
    assert alert is not None
    assert alert.status == "PENDING"
    assert "Recovered" in str(alert.last_error)


def test_cancel_current_alerts_can_filter_by_instrument(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "alerts.db")
    keep = store.create_alert(instrument="EUR_USD", target_price=1.2, direction="above")
    cancel = store.create_alert(instrument="XAU_USD", target_price=3000.0, direction="below")

    cancelled = store.cancel_current_alerts(instrument="gold")
    assert [alert.id for alert in cancelled] == [cancel.id]
    assert store.get_alert(cancel.id).status == "CANCELLED"
    assert store.get_alert(keep.id).status == "PENDING"

