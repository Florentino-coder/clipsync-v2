def test_import_legacy():
    from clipsync import legacy
    assert hasattr(legacy, "main")


def test_app_base_dir_is_pc_root():
    from clipsync.legacy import app_base_dir

    base = app_base_dir()
    assert base.name == "pc"
    assert (base / "assets").is_dir()
