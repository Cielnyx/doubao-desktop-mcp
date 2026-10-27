"""Package source and setup instructions; omit environments and local runtime data."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ("src", "scripts", "tests", "pyproject.toml", "requirements-lock.txt",
           "README.md", "INSTALL_FOR_AGENTS.md", "TEST_RESULTS.md", ".gitignore")


def main():
    target = ROOT / "dist" / "doubao-desktop-portable.zip"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in INCLUDE:
            item = ROOT / name
            files = sorted(item.rglob("*")) if item.is_dir() else [item]
            for file in files:
                relative = file.relative_to(ROOT)
                if file.is_file() and not any(p == "__pycache__" or p.endswith(".egg-info") for p in relative.parts):
                    bundle.write(file, "doubao-desktop/" + relative.as_posix())
    with zipfile.ZipFile(target) as bundle:
        assert bundle.testzip() is None
        assert all(".venv" not in Path(n).parts and ".runtime" not in Path(n).parts for n in bundle.namelist())
    print(target)


if __name__ == "__main__":
    main()
