from clipsync.relay_failover import RelaySelector


def test_starts_with_primary_and_stays_sticky_when_connected():
    selector = RelaySelector(["primary", "backup"])

    assert selector.current == "primary"
    selector.connected()
    assert selector.current == "primary"


def test_failure_moves_to_backup_once():
    selector = RelaySelector(["primary", "backup"])

    selector.failed()

    assert selector.current == "backup"


def test_next_failure_wraps_back_to_primary():
    selector = RelaySelector(["primary", "backup"])

    selector.failed()
    selector.failed()

    assert selector.current == "primary"


def test_empty_urls_use_safe_fallback():
    selector = RelaySelector([])

    assert selector.current == ""
    selector.failed()
    assert selector.current == ""
