def test_import_legacy():
    from clipsync import legacy
    assert hasattr(legacy, "main")
