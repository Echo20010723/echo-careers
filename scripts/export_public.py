"""Export the reviewed source and synthetic fixtures without local runtime files."""
import argparse
from pathlib import Path
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = set('''
Dockerfile render.yaml .dockerignore .gitignore README.md requirements.render.txt requirements.lock.txt
START.cmd start.ps1 pytest.ini
boss_connector/manifest.json boss_connector/reader.js boss_connector/observer.js boss_connector/worker.js
config/config.yaml config/error_messages.json config/logging_rules.json config/occupations.tsv
examples/resume_demo.md examples/jobs_demo.json examples/agent_jobs.json
docs/agent-evaluation.md docs/cross-industry-benchmark.md docs/hero-motion-prompt.md
scripts/launch.py scripts/evaluate_agent.py scripts/evaluate_cross_industry.py scripts/smoke_public.py scripts/export_public.py
tests/test_discovery.py tests/test_discovery_agent.py tests/test_public_workbench.py tests/test_cross_industry.py tests/test_offerdao.py tests/test_application_drafts.py tests/test_windows_launcher.py
app/static/hero-grain.png app/static/geist-pixel-circle.woff2 app/static/geist-pixel-OFL.txt
app/static/quicksand-latin-700.woff2 app/static/quicksand-OFL.txt
app/static/echo-logo.png app/static/echo-logo-small.png app/static/resume-demo.md app/static/jobs-example.json
'''.split())


def allowed_files():
    files = set(FILES)
    for pattern in ('app/*.py','app/static/*.js','app/static/*.css','app/static/*.html','prompts/*.txt'):
        files.update(file.relative_to(ROOT).as_posix() for file in ROOT.glob(pattern) if file.is_file())
    return files


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--check-git', action='store_true')
    args = parser.parse_args()
    allowed = allowed_files()
    if args.check_git:
        tracked = subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().rstrip('\0').split('\0')
        outside = sorted(set(filter(None, tracked)) - allowed)
        if outside:
            raise SystemExit('Review or remove these paths from the index before publishing:\n'+'\n'.join(outside))
        print('Tracked files match the public allowlist.')
    if args.output:
        for name in allowed:
            path = ROOT/name
            if not path.is_file() or path.resolve() != ROOT.resolve()/name:
                raise SystemExit('Expected a regular reviewed file: '+name)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(args.output, 'w', compression=ZIP_DEFLATED) as archive:
            for name in sorted(allowed):
                archive.write(ROOT/name,name)
        print(f'{len(allowed)} reviewed paths written to {args.output.resolve()}')


if __name__=='__main__':
    main()
