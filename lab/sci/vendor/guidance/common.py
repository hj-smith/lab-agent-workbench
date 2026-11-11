"""旧 analysis/sources/common.py 里 guidance 用到的 digest，原样。"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
