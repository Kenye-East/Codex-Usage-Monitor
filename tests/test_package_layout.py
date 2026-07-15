def test_entry_point_imports_without_build_tools():
    import usage_overlay.main

    assert callable(usage_overlay.main.main)
