"""Tag the tested commit and publish its artifacts; never replace a released version."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from package_release import release_version


def api(repo, path, data=None, missing_ok=False):
    request = Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise


def publish(repo, version, commit, dist, notes):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a release version and a full tested commit SHA")
    tag = f"v{version}"
    assets = [Path(dist) / f"UmaStudio-{version}{suffix}" for suffix in (".zip", ".zip.sha256")]
    for path in [*assets, Path(notes)]:
        if not path.is_file():
            raise FileNotFoundError(path)
    release = api(repo, f"releases/tags/{tag}", missing_ok=True)
    if release and not release["draft"]:
        print(f"{tag} is already published; leaving it unchanged.")
        return "published"
    ref = api(repo, f"git/ref/tags/{tag}", missing_ok=True)
    if ref:
        target = ref["object"]
        # Both lightweight and annotated tags must resolve to the tested commit.
        for _ in range(10):
            if target["type"] != "tag":
                break
            target = api(repo, f"git/tags/{target['sha']}")["object"]
        if target["type"] != "commit" or target["sha"] != commit:
            print(f"{tag} belongs to another commit; bump VERSION to publish a new release.")
            return "other-commit"
    else:
        api(repo, "git/refs", {"ref": f"refs/tags/{tag}", "sha": commit})
    if release:
        # Resume an interrupted upload only while the release is still a draft.
        subprocess.run(["gh", "release", "upload", tag, *map(str, assets),
                        "--repo", repo, "--clobber"], check=True)
    else:
        subprocess.run(["gh", "release", "create", tag, *map(str, assets),
                        "--repo", repo, "--verify-tag", "--draft",
                        "--title", f"UmaStudio {version}", "--notes-file", str(notes)], check=True)
    subprocess.run(["gh", "release", "edit", tag, "--repo", repo, "--draft=false"], check=True)
    print(f"Published {tag} from tested commit {commit}.")
    return "created"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    version = release_version(tag=f"v{args.version}")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    publish(os.environ["GH_REPO"], version, commit, args.dist,
            Path(__file__).resolve().parents[1] / "docs/releases" / f"{version}.md")


if __name__ == "__main__":
    main()
