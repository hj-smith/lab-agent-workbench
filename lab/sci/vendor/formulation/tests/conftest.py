"""让测试能 import lab.sci.vendor.formulation。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
