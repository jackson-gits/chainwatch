import sys
import time
print("Before sleep")
sys.stdout.flush()
time.sleep(10)
print("After sleep")
sys.stdout.flush()