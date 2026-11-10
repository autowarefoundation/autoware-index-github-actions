#!/usr/bin/env python3
"""Pick the Autoware container image stage that a set of ROS packages needs.

Prints `core-devel-cuda` when a package in the dependency closure of the
requested packages needs CUDA, and `core-devel` otherwise. The README of
this repository describes the signals.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

CUDA_IMAGE = "core-devel-cuda"
DEFAULT_IMAGE = "core-devel"

# The packages that only the CUDA image contains: the `core/cuda/*` entries of
# repositories/autoware.repos in autowarefoundation/autoware. Update this set
# when that list changes.
CUDA_PACKAGES = {
    "autoware_cuda_dependency_meta",
    "autoware_cuda_utils",
    "autoware_tensorrt_common",
    "cuda_blackboard",
    "negotiated",
    "negotiated_interfaces",
    "negotiated_examples",
}

# rosdep keys that install a CUDA toolkit or cuDNN from the Ubuntu archive.
CUDA_ROSDEP_KEYS = {
    "cuda-toolkit",
    "nvidia-cuda",
    "nvidia-cuda-dev",
    "nvidia-cudnn",
}

IGNORE_MARKERS = ("COLCON_IGNORE", "AMENT_IGNORE", "CATKIN_IGNORE")

# ROS package names (REP 144) plus upper case and hyphens, so that the name
# can go into a GitHub output and a workflow command without escaping.
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# exec_depend is not in this set: the image choice is about the build.
DEPEND_TAGS = {
    "depend",
    "build_depend",
    "build_export_depend",
    "buildtool_depend",
    "buildtool_export_depend",
    "test_depend",
}

_FLAGS = re.IGNORECASE
_NAME_END = r"(?![\w.+-])"

# Matched on CMake code with the contents of quoted arguments removed.
CMAKE_SIGNALS = [
    re.compile(rf"\bfind_package\s*\(\s*(CUDA|CUDAToolkit|TensorRT|CUDNN|spconv|cumm){_NAME_END}[^)]*\)", _FLAGS),
    re.compile(r"\b(enable_language|check_language)\s*\([^)]*\bCUDA\b[^)]*\)", _FLAGS),
    # The first argument of project() is the project name, so skip it.
    re.compile(r"\bproject\s*\(\s*[^\s)]+\s[^)]*\bCUDA\b[^)]*\)", _FLAGS),
    re.compile(r"\bcuda_add_(library|executable)\s*\(", _FLAGS),
]

# Matched on CMake code with the quoted arguments kept, because library and
# header names are often quoted.
CMAKE_LIBRARY_SIGNALS = [
    re.compile(
        r"\bfind_(library|path)\s*\([^)]*?(?<![\w-])"
        r"(nvinfer\w*|nvonnxparser|NvInfer\w*\.h|cudnn\w*(\.h)?|cudart|cuda_runtime\.h)(?![\w-])[^)]*\)",
        _FLAGS,
    ),
]

ADD_SUBDIRECTORY = re.compile(r"\badd_subdirectory\s*\(\s*(\"[^\"]*\"|[^\s)]+)", _FLAGS)
BRACKET_OPEN = re.compile(r"\[(=*)\[")


def printable(value: object) -> str:
    """Escape control characters, so that a file name cannot start a workflow command."""
    return str(value).encode("unicode_escape").decode("ascii")


def warn(message: str) -> None:
    print(f"::warning::{message}", file=sys.stderr)


def inside(path: Path, root: Path) -> bool:
    """Return whether path, with its symlinks resolved, is under root."""
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def find_packages(roots: list[Path]) -> dict[str, Path]:
    """Map package name to package directory, the way colcon finds packages.

    A directory with an ignore marker is skipped with everything below it.
    The search does not go below a package, so a package.xml in a test
    fixture of a package is no package.
    """
    packages: dict[str, Path] = {}
    for root in roots:
        for current, directories, files in os.walk(root):
            directories.sort()
            if any(marker in files for marker in IGNORE_MARKERS):
                directories.clear()
                continue
            if "package.xml" not in files:
                continue
            directories.clear()
            directory = Path(current)
            manifest = directory / "package.xml"
            if not inside(manifest, root):
                warn(f"{printable(manifest)} points outside the source path, skipping it")
                continue
            try:
                name = ET.parse(manifest).getroot().findtext("name")
            except (ET.ParseError, OSError) as error:
                warn(f"cannot parse {printable(manifest)}: {printable(error)}")
                continue
            name = (name or "").strip()
            if not PACKAGE_NAME.match(name):
                warn(f"{printable(manifest)} has no valid package name, skipping it")
                continue
            packages.setdefault(name, directory)
    return packages


def dependencies(directory: Path) -> set[str]:
    root = ET.parse(directory / "package.xml").getroot()
    return {(element.text or "").strip() for element in root if element.tag in DEPEND_TAGS} - {""}


def split_cmake(text: str) -> tuple[str, str]:
    """Remove the comments from CMake code.

    Returns the code two times: with the quoted and bracket arguments kept,
    and with their contents removed. A `#` inside such an argument starts no
    comment.
    """
    with_strings: list[str] = []
    without_strings: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = index + 1
            while end < length and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            end = min(end + 1, length)
            with_strings.append(text[index:end])
            without_strings.append('""')
            index = end
        elif character == "[" and (bracket := BRACKET_OPEN.match(text, index)):
            close = text.find(f"]{bracket.group(1)}]", bracket.end())
            end = length if close < 0 else close + len(bracket.group(1)) + 2
            with_strings.append(text[index:end])
            without_strings.append('""')
            index = end
        elif character == "#":
            bracket = BRACKET_OPEN.match(text, index + 1)
            if bracket:
                close = text.find(f"]{bracket.group(1)}]", bracket.end())
                index = length if close < 0 else close + len(bracket.group(1)) + 2
            else:
                newline = text.find("\n", index)
                index = length if newline < 0 else newline
        else:
            with_strings.append(character)
            without_strings.append(character)
            index += 1
    return "".join(with_strings), "".join(without_strings)


def cmake_files(directory: Path) -> list[Path]:
    """Return the CMake files that decide the image of the package in directory.

    These are its CMakeLists.txt, the CMakeLists.txt of each directory that
    it adds with a literal add_subdirectory() call, the .cmake files beside
    its CMakeLists.txt, and the .cmake files under its cmake/ directory
    (in any letter case). The add_subdirectory() calls of the added
    directories are not followed: vendored code in deeper directories can
    use CUDA in its own tests without the package needing it.
    """
    top = directory / "CMakeLists.txt"
    files = [top]
    if top.is_file() and inside(top, directory):
        code, _ = split_cmake(top.read_text(errors="replace"))
        for match in ADD_SUBDIRECTORY.finditer(code):
            files.append(directory / match.group(1).strip('"') / "CMakeLists.txt")
    files.extend(sorted(directory.glob("*.cmake")))
    for child in sorted(directory.iterdir()):
        if child.name.lower() == "cmake" and child.is_dir():
            files.extend(sorted(child.rglob("*.cmake")))
    return [path for path in dict.fromkeys(files) if path.is_file() and inside(path, directory)]


def cmake_signal(directory: Path) -> str | None:
    for path in cmake_files(directory):
        with_strings, without_strings = split_cmake(path.read_text(errors="replace"))
        for patterns, code in ((CMAKE_SIGNALS, without_strings), (CMAKE_LIBRARY_SIGNALS, with_strings)):
            for pattern in patterns:
                match = pattern.search(code)
                if match:
                    command = " ".join(match.group(0).split())
                    return f"{printable(path.relative_to(directory))} has `{command}`"
    return None


def detect(roots: list[Path], requested: list[str]) -> tuple[str, str]:
    packages = find_packages(roots)
    # A requested package that is not in the sources gets a warning and no
    # error: the build that follows reports it, with the results of the
    # other packages.
    missing = [name for name in requested if name not in packages]
    if missing:
        warn(f"packages not found under the source paths: {printable(' '.join(missing))}")
    pending = [name for name in requested if name in packages] if requested else sorted(packages)
    if not pending:
        warn("no package to read under the source paths, using the image without CUDA")
        return DEFAULT_IMAGE, "no package to read under the source paths"
    seen: set[str] = set()
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        directory = packages[name]
        depends = dependencies(directory)
        cuda_dependencies = sorted(depends & (CUDA_PACKAGES | CUDA_ROSDEP_KEYS))
        if cuda_dependencies:
            return CUDA_IMAGE, f"{name} depends on {', '.join(cuda_dependencies)}"
        signal = cmake_signal(directory)
        if signal:
            return CUDA_IMAGE, f"{name}: {signal}"
        pending.extend(sorted(depends & packages.keys()))
    return DEFAULT_IMAGE, f"no CUDA signal in {len(seen)} package(s): {' '.join(sorted(seen))}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", required=True, help="Source roots, separated by spaces or newlines.")
    parser.add_argument("--packages", default="", help="Package names, separated by spaces. Empty means all.")
    args = parser.parse_args()
    roots = [Path(path) for path in args.paths.split()]
    for root in roots:
        if not root.is_dir():
            sys.exit(f"::error::source path {printable(root)} is not a directory")
    stage, reason = detect(roots, args.packages.split())
    print(json.dumps({"base_image_stage": stage, "reason": reason}))


if __name__ == "__main__":
    main()
