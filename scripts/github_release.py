from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update a GitHub release and upload built artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish", help="Create/update a release and upload assets.")
    publish.add_argument("--repo", required=True, help="Repository in owner/name form.")
    publish.add_argument("--tag", required=True, help="Git tag associated with the release.")
    publish.add_argument("--target", required=True, help="Commit SHA the release/tag should point to.")
    publish.add_argument("--name", required=True, help="Release display name.")
    publish.add_argument("--assets-dir", required=True, help="Directory containing release assets.")
    publish.add_argument("--body", default="", help="Release body text.")
    publish.add_argument("--prerelease", action="store_true", help="Mark the release as a prerelease.")
    publish.add_argument(
        "--move-tag",
        action="store_true",
        help="Create/update the tag ref before publishing the release.",
    )

    return parser.parse_args()


def require_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN or GH_TOKEN is required")
    return token


def split_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise SystemExit(f"Invalid --repo value: {repo!r}")
    owner, name = repo.split("/", 1)
    return owner, name


def api_request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict | list | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[object | None, dict[str, str]]:
    request_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "clog-release-script",
    }
    if headers:
        request_headers.update(headers)

    payload = data
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"

    request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            if not raw:
                return None, dict(response.headers.items())
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(raw.decode("utf-8")), dict(response.headers.items())
            return raw, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


def api_json(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict | list | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    payload, _ = api_request(
        method,
        url,
        token,
        json_body=json_body,
        data=data,
        headers=headers,
    )
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return payload


def get_release_by_tag(owner: str, repo: str, tag: str, token: str) -> dict | None:
    url = f"{API_ROOT}/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    try:
        return api_json("GET", url, token)
    except RuntimeError as exc:
        if " 404 " in str(exc):
            return None
        raise


def ensure_tag(owner: str, repo: str, tag: str, target: str, token: str) -> None:
    ref_name = f"tags/{tag}"
    get_url = f"{API_ROOT}/repos/{owner}/{repo}/git/ref/{ref_name}"
    try:
        api_json("GET", get_url, token)
    except RuntimeError as exc:
        if " 404 " not in str(exc):
            raise
        create_url = f"{API_ROOT}/repos/{owner}/{repo}/git/refs"
        api_json(
            "POST",
            create_url,
            token,
            json_body={"ref": f"refs/{ref_name}", "sha": target},
        )
        return

    update_url = f"{API_ROOT}/repos/{owner}/{repo}/git/refs/{ref_name}"
    api_json("PATCH", update_url, token, json_body={"sha": target, "force": True})


def create_or_update_release(
    owner: str,
    repo: str,
    tag: str,
    target: str,
    name: str,
    body: str,
    prerelease: bool,
    token: str,
) -> dict:
    payload = {
        "tag_name": tag,
        "target_commitish": target,
        "name": name,
        "body": body,
        "draft": False,
        "prerelease": prerelease,
    }
    release = get_release_by_tag(owner, repo, tag, token)
    if release is None:
        create_url = f"{API_ROOT}/repos/{owner}/{repo}/releases"
        return api_json("POST", create_url, token, json_body=payload)

    update_url = f"{API_ROOT}/repos/{owner}/{repo}/releases/{release['id']}"
    return api_json("PATCH", update_url, token, json_body=payload)


def list_release_assets(owner: str, repo: str, release_id: int, token: str) -> list[dict]:
    assets: list[dict] = []
    page = 1
    while True:
        url = f"{API_ROOT}/repos/{owner}/{repo}/releases/{release_id}/assets?per_page=100&page={page}"
        payload, _ = api_request("GET", url, token)
        if payload is None:
            return assets
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected JSON array from {url}")
        assets.extend(payload)
        if len(payload) < 100:
            return assets
        page += 1


def delete_release_assets(owner: str, repo: str, release_id: int, token: str) -> None:
    for asset in list_release_assets(owner, repo, release_id, token):
        delete_url = f"{API_ROOT}/repos/{owner}/{repo}/releases/assets/{asset['id']}"
        api_request("DELETE", delete_url, token)


def asset_files(assets_dir: Path) -> list[Path]:
    if not assets_dir.is_dir():
        raise SystemExit(f"Missing assets directory: {assets_dir}")
    files = sorted(path for path in assets_dir.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"No assets found under {assets_dir}")
    return files


def upload_asset(upload_url: str, asset_path: Path, token: str) -> None:
    base_url = upload_url.split("{", 1)[0]
    content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    query = urllib.parse.urlencode({"name": asset_path.name})
    url = f"{base_url}?{query}"
    data = asset_path.read_bytes()
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
    }
    api_request("POST", url, token, data=data, headers=headers)


def publish_release(args: argparse.Namespace) -> int:
    token = require_token()
    owner, repo = split_repo(args.repo)
    assets_dir = Path(args.assets_dir)
    assets = asset_files(assets_dir)

    if args.move_tag:
        ensure_tag(owner, repo, args.tag, args.target, token)

    release = create_or_update_release(
        owner,
        repo,
        args.tag,
        args.target,
        args.name,
        args.body,
        args.prerelease,
        token,
    )
    release_id = int(release["id"])
    delete_release_assets(owner, repo, release_id, token)
    for asset in assets:
        upload_asset(str(release["upload_url"]), asset, token)
        print(f"uploaded {asset.name}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "publish":
        return publish_release(args)
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
