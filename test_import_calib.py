import sys
print("Before import")
try:
    from chainwatch.eval.protocol import calibrate_world
    print("Imported calibrate_world")
except Exception as e:
    print(f"Import error: {e}")
    sys.exit(1)
print("After import")