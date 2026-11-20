"""Source revision used by the Windows launcher to detect a stale backend."""
from hashlib import sha256
from pathlib import Path


def runtime_revision(root: Path) -> str:
    app = root / 'app'
    files = [*app.glob('*.py'), *(app / 'static').glob('*.html'),
             *(app / 'static').glob('*.js'), *(app / 'static').glob('*.css')]
    digest = sha256()
    for path in sorted(files):
        digest.update(path.relative_to(app).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
