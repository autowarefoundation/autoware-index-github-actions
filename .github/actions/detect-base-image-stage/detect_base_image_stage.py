#!/usr/bin/env python3
"""Pick the Autoware container image stage that a set of ROS packages needs.

Prints `core-devel-cuda` when a package in the dependency closure of the
requested packages needs CUDA, and `core-devel` otherwise. See action.yaml
for the signals.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

CUDA_IMAGE = "core-devel-cuda"
DEFAULT_IMAGE = "core-devel"

# The packages under src/core/cuda of Autoware. core-devel does not contain them.
CUDA_PACKAGES = {
    "autoware_cuda_dependency_meta",
    "autoware_cuda_utils",
    "autoware_tensorrt_common",
    "cuda_blackboard",
    "negotiated",
    "negotiated_interfaces",
    "negotiated_examples",
}

IGNORE_MARKERS = ("COLCON_IGNORE", "AMENT_IGNORE", "CATKIN_IGNORE")

# ROS package names (REP 144) plus upper case and hyphens, so that the name
# can go into a GitHub output and a workflow command without escaping.
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

DEPEND_TAGS = {
    "depend",
    "build_depend",
    "build_export_depend",
    "buildtool_depend",
    "test_depend",
}

CMAKE_SIGNALS = [
    re.compile(r"\bfind_package\s*\(\s*(CUDA|CUDAToolkit|TensorRT)\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\benable_language\s*\(\s*CUDA\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\bcheck_language\s*\(\s*CUDA\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\bproject\s*\([^)]*\bLANGUAGES\b[^)]*\bCUDA\b[^)]*\)", re.IGNORECASE),
]


def find_packages(roots: list[Path]) -> dict[str, Path]:
    """Map package name to package directory, skipping ignored trees."""
    packages: dict[str, Path] = {}
    for root in roots:
        for manifest in sorted(root.rglob("package.xml")):
            directory = manifest.parent
            relative = directory.relative_to(root)
            # relative.parents ends with ".", so the root itself is checked too.
            if any(
                (root / part / marker).exists()
                for part in [relative, *relative.parents]
                for marker in IGNORE_MARKERS
            ):
                continue
            try:
                name = ET.parse(manifest).getroot().findtext("name")
            except ET.ParseError as error:
                print(f"::warning::cannot parse {manifest}: {error}", file=sys.stderr)
                continue
            name = (name or "").strip()
            if not PACKAGE_NAME.match(name):
                print(f"::warning::{manifest} has no valid package name, skipping it", file=sys.stderr)
                continue
            packages.setdefault(name, directory)
    return packages


def dependencies(directory: Path) -> set[str]:
    root = ET.parse(directory / "package.xml").getroot()
    return {(element.text or "").strip() for element in root if element.tag in DEPEND_TAGS} - {""}


def strip_cmake_comments(text: str) -> str:
    text = re.sub(r"#\[(=*)\[.*?\]\1\]", "", text, flags=re.DOTALL)
    return re.sub(r"#[^\n]*", "", text)


def cmake_signal(directory: Path) -> str | None:
    files = [directory / "CMakeLists.txt", *sorted((directory / "cmake").glob("*.cmake"))]
    for path in files:
        if not path.is_file():
            continue
        text = strip_cmake_comments(path.read_text(errors="replace"))
        for pattern in CMAKE_SIGNALS:
            match = pattern.search(text)
            if match:
                command = " ".join(match.group(0).split())
                return f"{path.relative_to(directory)} has `{command}`"
    return None


def detect(roots: list[Path], requested: list[str]) -> tuple[str, str]:
    packages = find_packages(roots)
    if not packages:
        sys.exit("::error::no package with a valid package.xml found under the source paths")
    missing = [name for name in requested if name not in packages]
    if missing:
        sys.exit(f"::error::packages not found under the source paths: {' '.join(missing)}")
    pending = list(requested) if requested else sorted(packages)
    seen: set[str] = set()
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        directory = packages[name]
        cuda_dependencies = sorted(dependencies(directory) & CUDA_PACKAGES)
        if cuda_dependencies:
            return CUDA_IMAGE, f"{name} depends on {', '.join(cuda_dependencies)}"
        signal = cmake_signal(directory)
        if signal:
            return CUDA_IMAGE, f"{name}: {signal}"
        pending.extend(sorted(dependencies(directory) & packages.keys()))
    return DEFAULT_IMAGE, f"no CUDA signal in {len(seen)} package(s): {' '.join(sorted(seen))}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", required=True, help="Source roots, separated by spaces or newlines.")
    parser.add_argument("--packages", default="", help="Package names, separated by spaces. Empty means all.")
    args = parser.parse_args()
    roots = [Path(path) for path in args.paths.split()]
    for root in roots:
        if not root.is_dir():
            sys.exit(f"::error::source path {root} is not a directory")
    stage, reason = detect(roots, args.packages.split())
    print(json.dumps({"base_image_stage": stage, "reason": reason}))


if __name__ == "__main__":
    main()
