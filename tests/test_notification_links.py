from src.web.api.notifications import _normalize_link


def test_legacy_stock_notification_link_is_migrated_to_portfolio():
    assert (
        _normalize_link("/stocks?symbol=002436&market=CN")
        == "/portfolio?symbol=002436&market=CN"
    )


def test_notification_link_preserves_supported_destination():
    assert _normalize_link("/reports?date=2026-08-10") == "/reports?date=2026-08-10"
    assert _normalize_link(None) == ""
