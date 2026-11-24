"""Source revision used by the Windows launcher to detect a stale backend."""
from hashlib import sha256
from os.path import normcase
from pathlib import Path


def runtime_revision(root: Path) -> str:
    # Static files are served from disk on each request.
    files = [*(root / 'app').rglob('*.py'), *(root / 'config').glob('*.yaml')]
    digest = sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def project_identity(root: Path) -> str:
    return sha256(normcase(str(root.resolve())).encode('utf-8')).hexdigest()[:24]
