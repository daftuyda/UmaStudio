"""Create the Windows download without including local caches or game files."""
import argparse
import ast
import hashlib
from pathlib import Path
import re
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

HERE = Path(__file__).resolve().parent


def release_version(source=HERE / "serve.py", tag=""):
    assignments = ast.parse(Path(source).read_text(encoding="utf-8")).body
    versions = [ast.literal_eval(node.value) for node in assignments
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)]
    if len(versions) != 1 or not isinstance(versions[0], str) or not re.fullmatch(r"\d+\.\d+\.\d+", versions[0]):
        raise ValueError("serve.py must define one VERSION in major.minor.patch format")
    version = versions[0]
    if tag and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match app version v{version}")
    return version


def package(dist, version, repo=HERE.parent):
    dist, repo = Path(dist), Path(repo)
    app = dist / "UmaStudio"
    required = ("UmaStudio.exe", "_internal/static/index.html", "_internal/static/settings.js",
                "_internal/static/surface-collision.js")
    for relative in required:
        if not (app / relative).is_file():
            raise FileNotFoundError(f"Incomplete Windows build: {app / relative}")
    shutil.copyfile(repo / "LICENSE", app / "LICENSE")
    shutil.copyfile(repo / "README.md", app / "README.md")
    shutil.copyfile(repo / "docs/releases" / f"{version}.md", app / "RELEASE_NOTES.md")
    archive = dist / f"UmaStudio-{version}.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(dist).as_posix())
    with ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        for relative in required:
            bundle.getinfo(f"UmaStudio/{relative}")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii", newline="\n")
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-only", action="store_true")
    parser.add_argument("--tag", default="")
    parser.add_argument("--dist", type=Path, default=HERE / "dist")
    args = parser.parse_args()
    version = release_version(tag=args.tag)
    notes = HERE.parent / "docs/releases" / f"{version}.md"
    if not notes.is_file():
        raise FileNotFoundError(f"Add release notes before building: {notes}")
    if args.version_only:
        print(version)
    else:
        for path in package(args.dist, version):
            print(path)


if __name__ == "__main__":
    main()
