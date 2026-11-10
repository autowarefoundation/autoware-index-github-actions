"""Tests for the detect-base-image-stage script.

Run with `python3 -m unittest discover -s tests` from the repository root.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "actions"
    / "detect-base-image-stage"
    / "detect_base_image_stage.py"
)
_spec = importlib.util.spec_from_file_location("detect_base_image_stage", SCRIPT)
detector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(detector)

CUDA = detector.CUDA_IMAGE
PLAIN = detector.DEFAULT_IMAGE


class DetectTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def package(self, path: str, name: str | None = None, depends: str = "", cmake: str | None = "") -> Path:
        directory = self.root / path
        directory.mkdir(parents=True, exist_ok=True)
        name = Path(path).name if name is None else name
        manifest = f'<?xml version="1.0"?>\n<package format="3">\n<name>{name}</name>\n{depends}\n</package>\n'
        (directory / "package.xml").write_text(manifest)
        if cmake is not None:
            (directory / "CMakeLists.txt").write_text(f"cmake_minimum_required(VERSION 3.14)\n{cmake}\n")
        return directory

    def detect(self, packages: str = "") -> tuple[str, str, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            stage, reason = detector.detect([self.root], packages.split())
        return stage, reason, stderr.getvalue()

    def stage_of_cmake(self, cmake: str) -> str:
        self.package("a", cmake=cmake)
        return self.detect()[0]

    # package.xml signals

    def test_package_without_signal_gets_the_plain_image(self) -> None:
        self.package("a", depends="<depend>rclcpp</depend>", cmake="project(a)\nfind_package(ament_cmake REQUIRED)")
        stage, reason, _ = self.detect()
        self.assertEqual(stage, PLAIN)
        self.assertEqual(reason, "no CUDA signal in 1 package(s): a")

    def test_dependency_on_each_core_cuda_package_gets_the_cuda_image(self) -> None:
        for dependency in sorted(detector.CUDA_PACKAGES):
            with self.subTest(dependency=dependency):
                self.package("a", depends=f"<depend>{dependency}</depend>")
                stage, reason, _ = self.detect()
                self.assertEqual(stage, CUDA)
                self.assertEqual(reason, f"a depends on {dependency}")

    def test_build_and_test_depend_tags_count(self) -> None:
        tags = ("build_depend", "build_export_depend", "buildtool_depend", "buildtool_export_depend", "test_depend")
        for tag in tags:
            with self.subTest(tag=tag):
                self.package("a", depends=f"<{tag}>cuda_blackboard</{tag}>")
                self.assertEqual(self.detect()[0], CUDA)

    def test_exec_depend_does_not_count(self) -> None:
        self.package("a", depends="<exec_depend>autoware_tensorrt_common</exec_depend>")
        self.assertEqual(self.detect()[0], PLAIN)

    def test_cuda_rosdep_key_gets_the_cuda_image(self) -> None:
        self.package("a", depends="<depend>nvidia-cuda-dev</depend>")
        self.assertEqual(self.detect()[:2], (CUDA, "a depends on nvidia-cuda-dev"))

    # closure

    def test_closure_follows_a_local_dependency(self) -> None:
        self.package("a", depends="<depend>b</depend>")
        self.package("b", depends="<build_depend>autoware_cuda_utils</build_depend>")
        self.assertEqual(self.detect("a")[:2], (CUDA, "b depends on autoware_cuda_utils"))

    def test_requested_package_ignores_an_unrelated_cuda_package(self) -> None:
        self.package("a")
        self.package("b", depends="<depend>a</depend><depend>cuda_blackboard</depend>")
        self.assertEqual(self.detect("a")[0], PLAIN)

    def test_no_package_list_reads_every_package(self) -> None:
        self.package("a")
        self.package("b", depends="<depend>a</depend><depend>cuda_blackboard</depend>")
        self.assertEqual(self.detect()[:2], (CUDA, "b depends on cuda_blackboard"))

    def test_dependency_cycle_ends(self) -> None:
        self.package("a", depends="<depend>b</depend>")
        self.package("b", depends="<depend>a</depend>")
        self.assertEqual(self.detect("a")[0], PLAIN)

    # requested packages that are absent

    def test_absent_requested_package_warns_and_the_others_decide(self) -> None:
        self.package("a", depends="<depend>cuda_blackboard</depend>")
        stage, _, stderr = self.detect("zzz a")
        self.assertEqual(stage, CUDA)
        self.assertIn("::warning::packages not found under the source paths: zzz", stderr)

    def test_only_absent_requested_packages_get_the_plain_image(self) -> None:
        self.package("a", depends="<depend>cuda_blackboard</depend>")
        stage, reason, stderr = self.detect("zzz")
        self.assertEqual((stage, reason), (PLAIN, "no package to read under the source paths"))
        self.assertIn("zzz", stderr)

    def test_tree_without_packages_gets_the_plain_image_and_a_warning(self) -> None:
        stage, reason, stderr = self.detect()
        self.assertEqual((stage, reason), (PLAIN, "no package to read under the source paths"))
        self.assertIn("::warning::no package to read", stderr)

    # package discovery

    def test_ignore_markers_hide_a_tree(self) -> None:
        for marker in detector.IGNORE_MARKERS:
            with self.subTest(marker=marker):
                self.package("a")
                hidden = self.package(f"hidden_{marker}/b", name="b", depends="<depend>cuda_blackboard</depend>")
                (hidden.parent / marker).touch()
                self.assertEqual(self.detect()[0], PLAIN)

    def test_package_xml_below_a_package_is_no_package(self) -> None:
        self.package("a")
        self.package("a/test/fixture", name="fixture", depends="<depend>cuda_blackboard</depend>")
        stage, reason, _ = self.detect()
        self.assertEqual((stage, reason), (PLAIN, "no CUDA signal in 1 package(s): a"))

    def test_package_in_a_directory_with_a_dot_name_is_no_package(self) -> None:
        self.package("a")
        self.package(".github/fixture", name="q", depends="<depend>cuda_blackboard</depend>")
        stage, reason, _ = self.detect()
        self.assertEqual((stage, reason), (PLAIN, "no CUDA signal in 1 package(s): a"))

    def test_package_at_the_source_root(self) -> None:
        self.package(".", name="a", cmake="find_package(CUDA REQUIRED)")
        self.assertEqual(self.detect("a")[0], CUDA)

    def test_invalid_package_name_is_skipped(self) -> None:
        self.package("a", name="a\nbase_image_stage=evil")
        stage, _, stderr = self.detect()
        self.assertEqual(stage, PLAIN)
        self.assertIn("has no valid package name", stderr)

    def test_broken_manifest_is_skipped(self) -> None:
        self.package("a")
        (self.root / "a" / "package.xml").write_text("<package")
        self.package("b")
        stage, reason, stderr = self.detect()
        self.assertEqual((stage, reason), (PLAIN, "no CUDA signal in 1 package(s): b"))
        self.assertIn("cannot parse", stderr)

    def test_path_with_a_newline_cannot_start_a_workflow_command(self) -> None:
        directory = self.root / "x\n::error::forged"
        directory.mkdir()
        (directory / "package.xml").write_text("<package")
        self.package("a")
        _, _, stderr = self.detect()
        self.assertEqual(len(stderr.strip().splitlines()), 1)
        self.assertTrue(stderr.startswith("::warning::cannot parse "))

    def test_symlink_to_a_file_outside_the_root_is_not_read(self) -> None:
        outside = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        self.addCleanup(os.unlink, outside.name)
        outside.write("find_package(CUDA REQUIRED)\n")
        outside.close()
        self.package("a", cmake=None)
        (self.root / "a" / "CMakeLists.txt").symlink_to(outside.name)
        self.assertEqual(self.detect()[0], PLAIN)

    # CMake signals

    def test_cmake_calls_that_get_the_cuda_image(self) -> None:
        cases = [
            "find_package(CUDA)",
            "find_package(CUDA 11.0 REQUIRED)",
            "find_package(CUDAToolkit REQUIRED)",
            "Find_Package( TensorRT REQUIRED )",
            "find_package(CUDNN)",
            "find_package(spconv REQUIRED)",
            "find_package(cumm)",
            "enable_language(CUDA)",
            "enable_language(CXX CUDA)",
            "check_language(CUDA)",
            "project(a LANGUAGES CXX CUDA)",
            "project(a LANGUAGES CXX\n  CUDA)",
            "project(a CXX CUDA)",
            "project(${PROJECT_NAME} VERSION 1.0 LANGUAGES CUDA)",
            "cuda_add_library(a src/a.cu)",
            "cuda_add_executable(a src/a.cu)",
            "find_library(NVINFER nvinfer)",
            'find_library(NVINFER NAMES "nvinfer" REQUIRED)',
            "find_library(PARSER nvonnxparser)",
            "find_path(TENSORRT_INCLUDE_DIR NvInfer.h)",
            'find_path(X NAMES "NvInferRuntime.h" PATHS /usr/include)',
            "find_library(CUDNN_LIBRARY cudnn)",
            "find_path(CUDNN_INCLUDE cudnn.h)",
        ]
        for cmake in cases:
            with self.subTest(cmake=cmake):
                self.assertEqual(self.stage_of_cmake(cmake), CUDA)

    def test_cmake_text_that_keeps_the_plain_image(self) -> None:
        cases = [
            "# find_package(CUDA REQUIRED)",
            "#[[ enable_language(CUDA) ]]",
            "#[==[\nfind_package(CUDA)\n]==]",
            'message("call find_package(CUDA) yourself")',
            'message(STATUS "enable_language(CUDA) is off")',
            "message([[find_package(CUDA)]])",
            "message([==[enable_language(CUDA)]==])",
            'project(a DESCRIPTION "a CUDA free package" LANGUAGES CXX)',
            "project(cuda)",
            "project(CUDA LANGUAGES CXX)",
            "find_package(CUDA-x)",
            "find_package(CUDA_helpers)",
            "find_package(cuda_blackboard_helpers)",
            "find_package(OpenCV REQUIRED)",
            "find_library(X my_nvinfer_wrapper)",
            "find_library(X nvinfer-lite)",
        ]
        for cmake in cases:
            with self.subTest(cmake=cmake):
                self.assertEqual(self.stage_of_cmake(cmake), PLAIN)

    def test_hash_inside_a_string_starts_no_comment(self) -> None:
        self.assertEqual(self.stage_of_cmake('set(X "#[[")\nfind_package(CUDA REQUIRED)\nset(Y "]]")'), CUDA)
        self.assertEqual(self.stage_of_cmake('set(X "a # b") find_package(CUDA)'), CUDA)

    def test_escaped_quote_in_an_unquoted_argument_starts_no_string(self) -> None:
        cases = [
            'add_definitions(-DVERSION=\\"1.0\\")\nfind_package(CUDA REQUIRED)\nset(X "y")',
            'add_compile_definitions(NAME=\\"${PROJECT_NAME}\\")\ncuda_add_library(a a.cu)\nset(X "y")',
            'set(X \\# no comment) find_package(CUDA) set(Y "y")',
        ]
        for cmake in cases:
            with self.subTest(cmake=cmake):
                self.assertEqual(self.stage_of_cmake(cmake), CUDA)
        self.assertEqual(self.stage_of_cmake('message(a\\"b "find_package(CUDA)")'), PLAIN)

    def test_bracket_inside_an_unquoted_argument_starts_no_bracket_argument(self) -> None:
        cases = [
            "set(X a[[b)\nfind_package(CUDA REQUIRED)\nset(Y c]]d)",
            "set(X ${LIST}[[)\nenable_language(CUDA)",
            "set(X -DA=[=[)\nfind_package(TensorRT)\nset(Y ]=])",
        ]
        for cmake in cases:
            with self.subTest(cmake=cmake):
                self.assertEqual(self.stage_of_cmake(cmake), CUDA)
        self.assertEqual(self.stage_of_cmake('message("a"[[find_package(CUDA)]])'), PLAIN)
        self.assertEqual(self.stage_of_cmake("message(a [[find_package(CUDA)]])"), PLAIN)

    def test_cmake_files_in_the_cmake_directory_are_read(self) -> None:
        directory = self.package("a")
        (directory / "cmake" / "modules").mkdir(parents=True)
        (directory / "cmake" / "modules" / "gpu.cmake").write_text("find_package(TensorRT REQUIRED)\n")
        stage, reason, _ = self.detect()
        self.assertEqual(stage, CUDA)
        self.assertEqual(reason, "a: cmake/modules/gpu.cmake has `find_package(TensorRT REQUIRED)`")

    def test_added_subdirectory_is_read_one_level_deep(self) -> None:
        directory = self.package("a", cmake="add_subdirectory(src)")
        (directory / "src").mkdir()
        (directory / "src" / "CMakeLists.txt").write_text("enable_language(CUDA)\n")
        self.assertEqual(self.detect()[:2], (CUDA, "a: src/CMakeLists.txt has `enable_language(CUDA)`"))

    def test_quoted_subdirectory_is_read(self) -> None:
        directory = self.package("a", cmake='add_subdirectory("src")')
        (directory / "src").mkdir()
        (directory / "src" / "CMakeLists.txt").write_text("enable_language(CUDA)\n")
        self.assertEqual(self.detect()[0], CUDA)

    def test_cmake_file_beside_the_cmakelists_is_read(self) -> None:
        directory = self.package("a", cmake="include(gpu.cmake)")
        (directory / "gpu.cmake").write_text("find_package(CUDAToolkit REQUIRED)\n")
        self.assertEqual(self.detect()[:2], (CUDA, "a: gpu.cmake has `find_package(CUDAToolkit REQUIRED)`"))

    def test_cmake_directory_in_another_letter_case_is_read(self) -> None:
        directory = self.package("a")
        (directory / "CMake").mkdir()
        (directory / "CMake" / "gpu.cmake").write_text("find_package(CUDA REQUIRED)\n")
        self.assertEqual(self.detect()[0], CUDA)

    def test_symlinked_cmake_directory_outside_the_root_is_not_read(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (Path(outside.name) / "gpu.cmake").write_text("find_package(CUDA REQUIRED)\n")
        directory = self.package("a")
        (directory / "cmake").symlink_to(outside.name, target_is_directory=True)
        self.assertEqual(self.detect()[0], PLAIN)

    @unittest.skipIf(os.geteuid() == 0, "root can read every directory")
    def test_symlink_to_a_directory_outside_the_package_is_not_walked(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        # A directory that lists its files but refuses a stat of them: a
        # walk that reaches gpu.cmake fails with PermissionError.
        locked = Path(outside.name) / "locked"
        locked.mkdir()
        (locked / "gpu.cmake").write_text("find_package(CUDA REQUIRED)\n")
        locked.chmod(0o444)
        self.addCleanup(locked.chmod, 0o755)
        for link in ("cmake", "cmake/modules"):
            with self.subTest(link=link):
                directory = self.package(link.replace("/", "_"))
                (directory / link).parent.mkdir(exist_ok=True)
                (directory / link).symlink_to(outside.name, target_is_directory=True)
                self.assertEqual(self.detect()[0], PLAIN)

    def test_symlinked_manifest_outside_the_root_is_skipped(self) -> None:
        outside = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False)
        self.addCleanup(os.unlink, outside.name)
        outside.write('<package format="3"><name>a</name><depend>cuda_blackboard</depend></package>')
        outside.close()
        (self.root / "a").mkdir()
        (self.root / "a" / "package.xml").symlink_to(outside.name)
        stage, _, stderr = self.detect()
        self.assertEqual(stage, PLAIN)
        self.assertIn("points outside the source path", stderr)

    def test_vendored_code_two_levels_deep_is_not_read(self) -> None:
        directory = self.package("a", cmake="add_subdirectory(third_party/eigen)")
        tests = directory / "third_party" / "eigen" / "unsupported" / "test"
        tests.mkdir(parents=True)
        (directory / "third_party" / "eigen" / "CMakeLists.txt").write_text("add_subdirectory(unsupported/test)\n")
        (tests / "CMakeLists.txt").write_text("find_package(CUDA 9.0)\n")
        self.assertEqual(self.detect()[0], PLAIN)

    def test_subdirectory_that_is_not_added_is_not_read(self) -> None:
        directory = self.package("a")
        (directory / "third_party").mkdir()
        (directory / "third_party" / "CMakeLists.txt").write_text("find_package(CUDA 9.0)\n")
        self.assertEqual(self.detect()[0], PLAIN)

    # command line

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *arguments], capture_output=True, text=True, check=False
        )

    def test_command_line_prints_one_json_object(self) -> None:
        self.package("a", depends="<depend>cuda_blackboard</depend>")
        result = self.run_script(f"--paths={self.root}", "--packages=a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"base_image_stage": CUDA, "reason": "a depends on cuda_blackboard"}
        )

    def test_command_line_rejects_a_missing_source_path(self) -> None:
        result = self.run_script(f"--paths={self.root / 'nope'}")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a directory", result.stderr)

    # action.yaml

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
    def test_action_writes_the_outputs_and_the_job_summary(self) -> None:
        action = SCRIPT.with_name("action.yaml").read_text()
        run_block = textwrap.dedent(action.split("      run: |\n", 1)[1])
        self.package("src/a", depends="<depend>cuda_blackboard</depend>")
        environment = {
            "PATH": os.environ["PATH"],
            "SOURCE_PATHS": str(self.root / "src"),
            "PACKAGES": "a",
            "SCRIPT": str(SCRIPT),
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
        }
        result = subprocess.run(
            ["bash", "-c", run_block], env=environment, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "::notice::base_image_stage=core-devel-cuda (a depends on cuda_blackboard)\n")
        self.assertEqual(
            (self.root / "output").read_text(),
            "base_image_stage=core-devel-cuda\nreason=a depends on cuda_blackboard\n",
        )
        self.assertEqual(
            (self.root / "summary").read_text(),
            "### Base image stage: `core-devel-cuda`\n\nReason: a depends on cuda_blackboard\n",
        )


if __name__ == "__main__":
    unittest.main()
