import sys
print("Before any import")
sys.stdout.flush()
from chainwatch.config import load
print("After config import")
sys.stdout.flush()
from chainwatch.eval.protocol import calibrate_world
print("After protocol import")
sys.stdout.flush()