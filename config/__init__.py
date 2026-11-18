def load_tests(loader, tests, pattern):
    """Discover config tests without importing the guarded DB settings as a test."""
    from fnmatch import fnmatch
    from pathlib import Path

    directory = Path(__file__).parent
    for path in sorted(directory.iterdir()):
        if path.name == "test_settings.py":
            continue  # Settings, not a test module. Its import guard must remain active.
        if path.is_file() and path.suffix == ".py" and fnmatch(path.name, pattern or "test*.py"):
            tests.addTests(loader.loadTestsFromName(f"{__name__}.{path.stem}"))
        elif path.is_dir() and (path / "__init__.py").exists():
            tests.addTests(loader.discover(str(path), pattern or "test*.py", str(directory.parent)))
    return tests
