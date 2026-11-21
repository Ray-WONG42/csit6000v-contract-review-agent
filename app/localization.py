"""Deterministic translations for built-in report text; never translate quotations."""
import json
from pathlib import Path

EN = json.loads((Path(__file__).parent / 'locales' / 'en.json').read_text(encoding='utf-8'))


def text_for(value, language='zh'):
    return EN.get(value, value) if language == 'en' else value
