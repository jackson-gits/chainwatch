import sys
try:
    from chainwatch.config import load
    print("Imported config")
except Exception as e:
    print(f"Import error: {e}")
    sys.exit(1)

try:
    cfg = load(preset='medium')
    print("Loaded medium")
except Exception as e:
    print(f"Load error: {e}")
    sys.exit(1)

print("About to apply overrides")