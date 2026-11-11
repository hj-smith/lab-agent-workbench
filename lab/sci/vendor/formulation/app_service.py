"""迁移时新写的小文件：原 app_service.py（880 行，库服务层）不搬，这里只留被复制的算法文件用到的两个小函数，原文照抄。
dose_report.actual_summary 引用 number；preparation_actuals 引用 digest。"""
import hashlib
import json
import math


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()[:16]


def number(value, label, minimum=0):
    try:
        result=float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{label}必须是数字。')
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f'{label}必须是不小于 {minimum:g} 的有限数值。')
    return result
