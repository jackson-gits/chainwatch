import sys
print("Before import")
sys.stdout.flush()
from chainwatch.eval.protocol import calibrate_world
print("After import")
sys.stdout.flush()