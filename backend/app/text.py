"""短文本清洗。模型提示里的姓名、部门不能带换行。"""

from __future__ import annotations


def single_line(value: object, *, limit: int = 80) -> str:
    """折叠空白并截断。姓名里的换行不能把提示截成两段。"""

    return " ".join(str(value or "").split())[:limit]
