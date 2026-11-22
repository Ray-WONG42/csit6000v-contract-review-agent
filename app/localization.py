"""Deterministic translations for built-in report text; never translate quotations."""
import json
import re
from pathlib import Path

EN = json.loads((Path(__file__).parent / 'locales' / 'en.json').read_text(encoding='utf-8'))


def text_for(value, language='zh'):
    if language != 'en' or not isinstance(value, str):
        return value
    if value in EN:
        return EN[value]
    notice = re.fullmatch(r"示例供应商审计清单 v1 要求至少提前 30 个自然日。引用明确为 (\d+) 个自然日，请人工确认适用场景。", value)
    if notice:
        return f"Supplier demo policy v1 requires at least 30 calendar days' notice. The source states {notice[1]} calendar days; confirm applicability with a human reviewer."
    date = re.fullmatch(r"协议日期为 (.+)。", value)
    if date:
        return f"The agreement date is {date[1]}."
    return value
