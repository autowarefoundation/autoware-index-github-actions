# autoware-index-github-actions

Reusable GitHub Actions workflows for packages registered to [autoware-index](https://github.com/autowarefoundation/autoware-index).

One reusable workflow ships here:

- **`validate-package.yaml`**: *producer mode.* A community package's own CI calls it to validate "my latest code still builds against the current latest Autoware Core release." Includes ccache caching and a clang-tidy job. A repository hosting several registered packages calls it once per package via a matrix (see the example below).

The registry-side counterpart, `sweep-repository.yaml`, used to live here too. It now lives in [autoware-index](https://github.com/autowarefoundation/autoware-index) alongside the recorder that parses its output, so the two halves of that contract change together.

It pins the container to:

```
ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<autoware_version>
```

`base_image_stage` is **optional**. When unset (the default), the `resolve` job runs the [`detect-base-image-stage`](#detect-base-image-stage-composite-action) composite action on the checked-out repository. It picks `core-devel-cuda` when a package of the repository needs CUDA, and the lighter `core-devel` otherwise. A non-empty `base_image_stage` skips the detection.

`autoware_version` is **optional**. When unset (the default), a `resolve` job invokes the [`latest-autoware-version`](#latest-autoware-version-composite-action) composite action so callers don't have to bump CI per release. If a caller already knows the version (e.g. a release branch validating against a specific Autoware), passing `autoware_version: "1.8.0"` explicitly skips the action entirely.

Registered packages must **not** ship a `build_depends.repos`; the pinned image already contains the matching Autoware Core build, so none of the workflows exposes a `build_depends_repos` input.

## Usage

### Producer mode (`validate-package.yaml`)

In your package's `.github/workflows/autoware-index-validate.yaml`:

```yaml
name: autoware-index-validate
on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    strategy:
      fail-fast: false
      matrix:
        include:
          - ros_distro: jazzy
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/validate-package.yaml@main
    with:
      ros_distro: ${{ matrix.ros_distro }}
      package_name: autoware_livox_tag_filter
      pull-ccache: ${{ github.event_name != 'push' || github.ref != 'refs/heads/main' }}
      push-ccache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
      cache-key-element: build
      concurrency-group: autoware-index-validate-${{ matrix.ros_distro }}-${{ github.event_name == 'push' && 'main' || github.ref }}
```

The workflow runs `actions/checkout` on the calling repository, resolves the Autoware version, builds the named package against that pinned image, tests it, and runs clang-tidy in a follow-up job (artifact `clang-tidy-result-<ros_distro>-<package_name>-<resolved_version>`).

The runner's `$GITHUB_WORKSPACE` is the colcon workspace, with the repository checked out at `src/<repository-name>`. Dependency installation, builds, and tests run from the workspace root. For example, `tier4/tamagawa_imu_driver` is checked out at `src/tamagawa_imu_driver`. Generated `build/`, `install/`, and `log/` directories and the colcon mixin checkout are siblings of `src/`, so packages at the repository root can use normal ament lint discovery without scanning CI-generated files.

Both build and clang-tidy jobs use this layout. Build caches are separated by layout, package, and Autoware version. The clang-tidy action receives this checkout path through its `source-directory` input. It downloads the selected config and runs file discovery, ignore matching, and analysis from the source directory, using the compilation database at the workspace root. Repository-relative `.clang-tidy-ignore` patterns keep their existing meaning.

A repository hosting **several registered packages** validates each independently with a matrix; every leg gets its own concurrency group and clang-tidy artifact automatically:

```yaml
jobs:
  validate:
    strategy:
      fail-fast: false
      matrix:
        package_name: [autoware_a_filter, zz_planner_b]
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/validate-package.yaml@main
    with:
      ros_distro: jazzy
      package_name: ${{ matrix.package_name }}
```

## Workflow inputs

### `validate-package.yaml`

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | - | ROS 2 distribution (e.g. `jazzy`) |
| `autoware_version` | | resolved at runtime | Autoware Core SemVer (e.g. `1.8.0`); drives the container tag. Leave unset to auto-resolve. |
| `package_name` | ✓ | - | ROS package name. The build uses `colcon --packages-above-and-dependencies`, and the test uses `--packages-above` |
| `base_image_stage` | | detected at runtime | Container image stage, `core-devel` or `core-devel-cuda`. Leave unset to detect it from the package sources. |
| `runs-on` | | `'["ubuntu-24.04"]'` | Runner label as a JSON-encoded array |
| `pull-ccache` | | `true` | Restore ccache from GHA cache before build |
| `push-ccache` | | `false` | Save ccache to GHA cache after build (typically `true` only on push-to-main) |
| `cache-key-element` | | `default` | Extra discriminator baked into the ccache key |
| `concurrency-group` | | per-(run, package) | Concurrency group for this workflow_call invocation. The default isolates matrix legs of one caller run from each other. |
| `cancel-in-progress` | | `false` | Whether to cancel in-progress runs in the group |
| `clang_tidy_config_url` | | autoware `main` `.clang-tidy-ci` | URL of the `.clang-tidy` config used by the clang-tidy job |

## `latest-autoware-version` (composite action)

`.github/actions/latest-autoware-version/action.yaml` returns the SemVer of the freshest [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/releases) release whose `ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<version>` image is already published on GHCR. Releases ship before images, so the action walks releases newest-first and returns the first SemVer with a pullable image, sliding past the release-vs-image gap.

`validate-package.yaml` invokes it in its `resolve` job (skipping it entirely when the caller supplied an explicit `autoware_version`). You can also call it directly from your own workflow if you need the version for something else.

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | - | ROS 2 distribution (e.g. `jazzy`) |
| `base_image_stage` | | `core-devel` | Container stage for the existence probe |
| `max_lookback` | | `5` | How many recent autoware releases to scan |
| `github_token` | | `${{ github.token }}` | Used for the releases API to lift the anonymous rate limit |

**Output:** `autoware_version`, the resolved SemVer, e.g. `1.8.0`. Fails loudly if none of the last `max_lookback` releases has a published image.

Direct invocation:

```yaml
- id: latest
  uses: autowarefoundation/autoware-index-github-actions/.github/actions/latest-autoware-version@main
  with:
    ros_distro: jazzy
- run: echo "Using autoware ${{ steps.latest.outputs.autoware_version }}"
```

## `detect-base-image-stage` (composite action)

`.github/actions/detect-base-image-stage/action.yaml` returns the image stage that a set of packages needs. It finds the packages under the source paths the way colcon does: a directory with a `COLCON_IGNORE`, `AMENT_IGNORE` or `CATKIN_IGNORE` file is skipped, and a `package.xml` below another package is no package. It starts from the requested packages and follows their dependencies to the other packages under the same paths. A requested package that is not found gets a warning, and the other packages decide. The build that follows reports the absent package.

`validate-package.yaml` passes no package list, so every package of the repository counts. The reason is that `colcon-build` also builds the packages above `package_name`. In a repository with one CUDA package, every package therefore builds in `core-devel-cuda`.

**To get the CUDA image, declare it in `package.xml`:**

```xml
<depend>autoware_cuda_dependency_meta</depend>
```

This is the convention of Autoware for a package that uses CUDA, and it does not depend on how the CMake code is written. The other signals below are a second net for packages that do not follow the convention.

A package in the closure needs CUDA when one of these is true:

- Its `package.xml` has a build, build tool or test dependency on a package that only the CUDA image contains: `autoware_cuda_dependency_meta`, `autoware_cuda_utils`, `autoware_tensorrt_common`, `cuda_blackboard`, `negotiated`, `negotiated_interfaces` or `negotiated_examples`. These are the `core/cuda/*` entries of [`autoware.repos`](https://github.com/autowarefoundation/autoware/blob/main/repositories/autoware.repos).
- Its `package.xml` has such a dependency on one of the rosdep keys `cuda-toolkit`, `nvidia-cuda`, `nvidia-cuda-dev` or `nvidia-cudnn`.
- One of its CMake files uses CUDA or a library that only the CUDA image has:
  - `find_package()` of `CUDA`, `CUDAToolkit`, `TensorRT`, `CUDNN`, `spconv` or `cumm`
  - `enable_language(CUDA)` or `check_language(CUDA)`, or `CUDA` as a language in `project()`
  - `cuda_add_library()` or `cuda_add_executable()`
  - `find_library()` or `find_path()` of `nvinfer`, `nvonnxparser`, `NvInfer.h`, `cudnn`, `cudart` or `cuda_runtime.h`

The CMake files of a package are its `CMakeLists.txt`, the `CMakeLists.txt` of each directory that it adds with a literal `add_subdirectory()` call, the `.cmake` files beside its `CMakeLists.txt`, and the `.cmake` files under its `cmake/` directory. The `add_subdirectory()` calls of an added directory are not followed. Vendored code in deeper directories can call `find_package(CUDA)` without the package needing it, for example the tests of a vendored Eigen. Comments and the contents of quoted strings and bracket arguments are removed before the match, except for the library and header names.

Known limits:

- The detection does not evaluate CMake. A `find_package(CUDA)` behind an option that is off, or without `REQUIRED`, still gives `core-devel-cuda`.
- CUDA in a CMake file that is reached through a variable, through two levels of `add_subdirectory()`, or through `CONFIG_EXTRAS` is not found.
- `condition=` attributes in `package.xml` are not evaluated, so a dependency with a false condition still counts.
- A `.cmake` file in another directory than the package directory and `cmake/` is not read.
- `exec_depend` is no signal, because the image choice is about the build.

If the detection is wrong for a repository, the caller of `validate-package.yaml` passes `base_image_stage`. The job summary of the `resolve` job shows the stage and the reason.

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `paths` | ✓ | - | Source roots, separated by spaces or newlines |
| `packages` | | all packages | Package names to start from, separated by spaces |

**Outputs:** `base_image_stage` (`core-devel-cuda` or `core-devel`) and `reason` (the package and the signal that decided it). The action also prints both as a notice and writes them to the job summary.

The unit tests of the script are in `tests/`. Run them with `python3 -m unittest discover -s tests`.

The CUDA image is much larger. `core-devel-cuda-jazzy` is 8.6 GB compressed and 23.8 GB unpacked, and `core-devel-jazzy` is 1.8 GB compressed. A job container pulls the image before any step runs, so no step can free disk space first. Nobody tested the CUDA image on the default `ubuntu-24.04` runner yet. If the pull runs out of disk space, pass a larger runner with `runs-on`.

The CUDA image has CUDA, TensorRT, spconv and the packages under `src/core/cuda` of Autoware. It does not have ONNX Runtime. A package that uses ONNX Runtime must bring it.

## Container image convention

These images are published from [`autowarefoundation/autoware/docker`](https://github.com/autowarefoundation/autoware/tree/main/docker) per release. The `core-devel` stage ships `/opt/autoware/` already populated with compiled `autoware_core` and its build dependencies, so the workflow never fetches or rebuilds core itself; it just builds your package on top. The `core-devel-cuda` stage adds CUDA, TensorRT, spconv and the packages under `src/core/cuda` of Autoware (`autoware_cuda`, `cuda_blackboard` and `negotiated`).

The release-vs-image race is handled by `latest-autoware-version`'s walk (see above). If every recent release lacks an image, the action fails loudly with the list of skipped versions; callers can work around it by passing `autoware_version: "..."` explicitly until the image lands.

## Related repositories

- [`autoware-index`](https://github.com/autowarefoundation/autoware-index): the registry. Its sweep uses the `latest-autoware-version` action from this repo and hosts its own `sweep-repository.yaml`.
- [`autoware-github-actions`](https://github.com/autowarefoundation/autoware-github-actions) provides the composite actions `validate-package.yaml` chains (`remove-exec-depend`, `get-self-packages`, `colcon-build`, `colcon-test`, `clang-tidy`).
- [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/tree/main/docker): where the container images are built.
