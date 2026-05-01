from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_ROOT = DIST / "_package"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble a single release archive containing the GUI bundle and CLI binary."
    )
    parser.add_argument(
        "--channel",
        required=True,
        choices=("release", "nightly"),
        help="Release channel used for the output archive naming.",
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=("windows-x64", "linux-x64"),
        help="Target platform identifier used in the archive name.",
    )
    parser.add_argument(
        "--version",
        help="Version label for release builds, such as v1.2.3.",
    )
    return parser.parse_args()


def is_windows_platform(platform: str) -> bool:
    return platform.startswith("windows-")


def executable_name(base: str, platform: str) -> str:
    return f"{base}.exe" if is_windows_platform(platform) else base


def archive_suffix(platform: str) -> tuple[str, str]:
    if is_windows_platform(platform):
        return "zip", ".zip"
    return "gztar", ".tar.gz"


def version_label(channel: str, version: str | None) -> str:
    if channel == "nightly":
        return "nightly"
    if not version:
        raise SystemExit("--version is required when --channel=release")
    return version


def emit_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def stage_bundle(platform: str) -> Path:
    gui_bundle = DIST / "clog"
    cli_binary = DIST / executable_name("clog-cli", platform)
    if not gui_bundle.is_dir():
        raise SystemExit(f"Missing GUI bundle: {gui_bundle}")
    if not cli_binary.is_file():
        raise SystemExit(f"Missing CLI binary: {cli_binary}")

    stage_dir = PACKAGE_ROOT / platform
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    bundle_stage = stage_dir / "clog"
    shutil.copytree(gui_bundle, bundle_stage)
    shutil.copy2(cli_binary, bundle_stage / cli_binary.name)
    return stage_dir


def build_archive(channel: str, platform: str, version: str | None) -> Path:
    stage_dir = stage_bundle(platform)
    archive_format, extension = archive_suffix(platform)
    label = version_label(channel, version)
    archive_name = f"clog-{label}-{platform}"
    archive_base = DIST / archive_name
    archive_path = DIST / f"{archive_name}{extension}"

    if archive_path.exists():
        archive_path.unlink()

    created = shutil.make_archive(
        base_name=str(archive_base),
        format=archive_format,
        root_dir=stage_dir,
        base_dir="clog",
    )
    return Path(created)


def main() -> int:
    args = parse_args()
    archive_path = build_archive(args.channel, args.platform, args.version)
    emit_output("archive_path", str(archive_path))
    emit_output("archive_name", archive_path.name)
    print(archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
