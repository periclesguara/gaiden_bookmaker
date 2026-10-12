import hashlib
import os
import tempfile
from pathlib import Path


def digest(value):
    return hashlib.sha256(value).hexdigest()


def root():
    value = os.environ.get("GAIDEN_STORAGE_ROOT") or os.environ.get("GAIDEN_DATA_ROOT")
    if not value:
        raise ValueError("Configure external GAIDEN_STORAGE_ROOT")
    return Path(value).expanduser().resolve()


def verified_path(value, expected=None):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root() / path
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Symlink corpus paths are forbidden")
    path = path.resolve()
    if not path.is_relative_to(root()) or not path.is_file():
        raise ValueError("Corpus file missing or outside approved storage")
    if expected and digest(path.read_bytes()) != expected:
        raise ValueError("Corpus content hash mismatch")
    return path


def write_immutable(relative, content):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid corpus artifact path")
    path = root() / "corpus" / relative
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Symlink corpus paths are forbidden")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        verified_path(path, digest(content))
        return path
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".corpus-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        # A concurrent writer cannot replace an immutable artifact.
        os.link(temporary, path)
    except FileExistsError:
        verified_path(path, digest(content))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
