import os

# Must run before `app.main` is imported: tests must never touch a real database file.
os.environ["SENTINEL_DB"] = ":memory:"
