"""Isolated, reproducible CLI acceptance test. Never reads private contracts."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Use the configured real TACC model, not the rule demo.')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='clause-demo-') as directory:
        try:
            run(directory, args)
        finally:
            import sys
            if 'app.db' in sys.modules:
                sys.modules['app.db'].engine.dispose()


def run(directory, args):
    os.environ['APP_DATA'] = directory
    os.environ['APP_DEMO'] = '1'
    if not args.live:
        os.environ['MODEL_MODE'] = 'demo'
    from app.config import settings
    from app.db import initialize, session, User
    from app.security import seed_users
    from app.assistant import run_assistant
    from sqlalchemy import select
    initialize()
    seed_users()
    if args.live and settings.model_mode != 'tacc':
        raise SystemExit('Set MODEL_MODE=tacc and configure TACC before --live.')
    with session() as db:
        user = db.scalar(select(User).where(User.email == 'legal@example.com'))
        user_id = user.id
    memory, events = {}, []
    def checkpoint(key, value=None):
        if value is not None:
            memory[key] = value
        return memory.get(key)
    def event(stage, title, detail=''):
        events.append(stage)
        print(json.dumps({'stage': stage, 'title': title}, ensure_ascii=True), flush=True)
    message = ('请调用 create_document 工具生成一个名为 Demo 的 Word 文档，正文为：Contract review demo succeeded。然后告诉我保存结果。'
               if args.live else '/创建Skill {"name":"Reproduction Demo","categories":[],"focus":"Check payment deadlines","policy":"evidence-only@1"}')
    result = run_assistant(user_id, {'message': message, 'history': [], 'allow_web': False},
                           'reproduction-demo', event, checkpoint)
    assert 'tool' in events and result['artifacts'], result
    if not args.live:
        from engine import parse, review, Model, DEFAULT_SKILL
        raw = (Path(__file__).resolve().parent / 'fixtures/Acme-Service-Agreement.txt').read_bytes()
        reviewed = review(parse('demo.txt', raw), DEFAULT_SKILL, 'standard review', Model(event, 'demo'))
        assert len(reviewed['rows']) == 8, reviewed
    print(json.dumps({'status': 'PASS', 'mode': settings.model_mode,
                      'tool_called': True, 'artifact_count': len(result['artifacts'])}, ensure_ascii=True))


if __name__ == '__main__':
    main()
