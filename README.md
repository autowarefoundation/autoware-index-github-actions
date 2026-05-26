# autoware-index-github-actions

Reusable GitHub Actions workflows for packages registered to [autoware-index](https://github.com/autowarefoundation/autoware-index).

Two reusable workflows ship here, each with one calling pattern:

- **`validate-package.yaml`** — *producer mode.* A community package's own CI calls it to validate "my latest code still builds against pinned Autoware Core X.Y.Z." Includes ccache caching and a clang-tidy job.
- **`sweep-package.yaml`** — *sweep mode.* The autoware-index registry's sweep workflows call it to validate "package P at ref R still builds against pinned Autoware Core X.Y.Z." Clones the package explicitly, uploads a per-row result artifact for the registry's recorder, and exports `resolved_sha`.

Both pin the container to:

```
ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<autoware_version>
```

Registered packages must **not** ship a `build_depends.repos` — the pinned image already contains the matching Autoware Core build, so neither workflow exposes a `build_depends_repos` input.

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
            autoware_version: "1.8.0"
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/validate-package.yaml@main
    with:
      ros_distro: ${{ matrix.ros_distro }}
      autoware_version: ${{ matrix.autoware_version }}
      package_name: autoware_livox_tag_filter
      pull-ccache: ${{ github.event_name != 'push' || github.ref != 'refs/heads/main' }}
      push-ccache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
      cache-key-element: build
      concurrency-group: autoware-index-validate-${{ matrix.ros_distro }}-${{ matrix.autoware_version }}-${{ github.event_name == 'push' && 'main' || github.ref }}
```

The workflow runs `actions/checkout` on the calling repository, builds the named package against the pinned image, tests it, and runs clang-tidy in a follow-up job (artifact `clang-tidy-result-<ros_distro>-<autoware_version>`).

### Sweep mode (`sweep-package.yaml`)

Called from the autoware-index registry's sweep workflows. The registry knows the package's URL and the exact ref to test, so both are passed explicitly:

```yaml
jobs:
  sweep:
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/sweep-package.yaml@main
    with:
      ros_distro: jazzy
      autoware_version: "1.8.0"
      package_name: autoware_livox_tag_filter
      package_repository: https://github.com/autowarefoundation/autoware_livox_tag_filter
      package_ref: "0.2.1"
```

The job uploads `validate-result-<ros_distro>-<package_name>-<autoware_version>` containing `result.json` (`build_outcome`, `test_outcome`, `resolved_sha`, the inputs). The registry's recorder (`scripts/build_envelopes.py` in autoware-index) consumes this exact artifact-name layout — don't rename without bumping the recorder.

`resolved_sha` is also exported as a workflow output for callers that prefer reading it directly.

## Workflow inputs

### `validate-package.yaml` (producer)

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | — | ROS 2 distribution (e.g. `jazzy`) |
| `autoware_version` | ✓ | — | Autoware Core SemVer (e.g. `1.8.0`); drives the container tag |
| `package_name` | ✓ | — | ROS package name; passed to `colcon --packages-up-to` |
| `base_image_stage` | | `core-devel` | Container image stage |
| `runs-on` | | `'["ubuntu-24.04"]'` | Runner label as a JSON-encoded array |
| `pull-ccache` | | `true` | Restore ccache from GHA cache before build |
| `push-ccache` | | `false` | Save ccache to GHA cache after build (typically `true` only on push-to-main) |
| `cache-key-element` | | `default` | Extra discriminator baked into the ccache key |
| `concurrency-group` | | per-run | Concurrency group for this workflow_call invocation |
| `cancel-in-progress` | | `false` | Whether to cancel in-progress runs in the group |
| `clang_tidy_config_url` | | autoware `main` `.clang-tidy-ci` | URL of the `.clang-tidy` config used by the clang-tidy job |

### `sweep-package.yaml` (sweep)

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | — | ROS 2 distribution |
| `autoware_version` | ✓ | — | Autoware Core SemVer; drives the container tag |
| `package_name` | ✓ | — | ROS package name; passed to `colcon --packages-up-to` |
| `package_repository` | ✓ | — | Git URL of the package to clone |
| `package_ref` | ✓ | — | Tag, branch, or sha of `package_repository` to check out |
| `base_image_stage` | | `core-devel` | Container image stage |
| `runs-on` | | `'["ubuntu-24.04"]'` | Runner label as a JSON-encoded array |

**Output:** `resolved_sha` — the exact commit SHA that was checked out and built.

## Container image convention

These images are published from [`autowarefoundation/autoware/docker`](https://github.com/autowarefoundation/autoware/tree/main/docker) per release. The `core-devel` stage ships `/opt/autoware/` already populated with compiled `autoware_core` and its build dependencies, so neither workflow fetches or rebuilds core itself — they just build your package on top.

If the requested tag doesn't exist (e.g. `core-devel-jazzy-1.9.0` before `1.9.0` ships), the container pull fails with `manifest unknown`. That's the intended failure signal — the autoware-index registry's tag poller should only register a new `autoware_version` after the corresponding image is published.

## Related repositories

- [`autoware-index`](https://github.com/autowarefoundation/autoware-index) — the registry whose sweep workflows consume `sweep-package.yaml`.
- [`autoware-github-actions`](https://github.com/autowarefoundation/autoware-github-actions) — provides the composite actions this workflow chains (`remove-exec-depend`, `get-self-packages`, `colcon-build`, `colcon-test`, `clang-tidy`).
- [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/tree/main/docker) — source of the container images.
