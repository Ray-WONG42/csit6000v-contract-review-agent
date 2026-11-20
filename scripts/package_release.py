"""Build a public allowlisted ZIP; never package the workspace wholesale."""
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DIRECTORIES = {'app', 'prompts', 'scripts', 'tacc', 'tests', 'fixtures', 'evals', 'migrations', 'deploy', 'docs', 'web'}
FILES = {'README.md', 'LICENSE', 'NOTICE', 'AGENTS.md', '.gitignore', '.gitattributes', '.dockerignore', '.env.example',
         'requirements.txt', 'requirements-lock.txt', 'demo.py', 'run_agent.sh', 'prompt_loader.py',
         'engine.py', 'mailbox.py', 'alembic.ini', 'pytest.ini', 'compose.yaml', 'Dockerfile',
         'Setup.cmd', 'Start.cmd', 'Start-TACC.cmd', 'Stop-TACC.cmd', 'setup.ps1', 'start.ps1'}
SKIP = {'node_modules', '__pycache__', '.pytest_cache', 'configurations', 'test-results', 'playwright-report',
        'data', 'uploads', 'outputs', 'logs', 'traces', 'memory', 'sessions', 'models', 'weights', '.cache', 'secrets'}
PRIVATE_DOCS = {'progress.md', 'workbench.jpg', 'assistant.jpg'}
TEXT = {'.py', '.txt', '.md', '.json', '.yaml', '.yml', '.toml', '.conf', '.sh', '.ps1', '.cmd', '.ts', '.tsx', '.js', '.css', '.html', '.ini'}


def allowed(path):
    rel = path.relative_to(ROOT)
    if any(part in SKIP for part in rel.parts) or path.is_symlink():
        return False
    if rel.parts[0] not in DIRECTORIES and str(rel) not in FILES:
        return False
    if rel.parts[0] == 'docs' and path.name in PRIVATE_DOCS:
        return False
    if path.name.startswith('.env') and path.name != '.env.example':
        return False
    if path.name.startswith(('id_rsa', 'id_ed25519')):
        return False
    if path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.log', '.pyc', '.gguf', '.safetensors', '.bin', '.bak', '.zip', '.tar', '.gz'}:
        return False
    if rel.parts[0] == 'tacc' and path.name in {'run.sh', 'request.json'}:
        return False
    return True


def main():
    paths = sorted(p for p in ROOT.rglob('*') if p.is_file() and allowed(p))
    violations = []
    for path in paths:
        if path.suffix in TEXT or path.name in {'.env.example', '.gitignore'}:
            text = path.read_text(encoding='utf-8')
            if re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----', text):
                violations.append(str(path.relative_to(ROOT)) + ': private key')
            if re.search(r'[A-Za-z]:[\\/]+Users[\\/]', text):
                violations.append(str(path.relative_to(ROOT)) + ': personal absolute path')
            if path.name == '.env.example' and re.search(r'(?im)^[A-Z_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[ \t]*=[ \t]*[^\s#]+', text):
                violations.append('.env.example: nonempty secret')
    if violations:
        raise SystemExit('\n'.join(violations))
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Build web/dist before packaging.')
    output = ROOT / 'release'
    output.mkdir(exist_ok=True)
    destination = output / 'contract-review-agent-github.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print('PASS: {} allowlisted files; no detected private artifacts. Output: release/{}'.format(len(paths), destination.name))


if __name__ == '__main__':
    main()
