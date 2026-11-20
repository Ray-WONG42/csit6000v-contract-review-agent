"""Load version-controlled prompts relative to this module, including on TACC."""
from pathlib import Path


def load_prompt(name):
    if name not in {'assistant_system', 'contract_system', 'survey', 'tool', 'reflection'}:
        raise ValueError('Unknown prompt template')
    return (Path(__file__).resolve().parent / 'prompts' / (name + '.txt')).read_text(encoding='utf-8').strip()
