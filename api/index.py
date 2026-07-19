"""Vercelが読み込む単一Python Function。ロジックはcore packageに置く。"""

from astrolabe.public_api.handler import handler

__all__ = ["handler"]
