# autoware-index-github-actions

Reusable GitHub Actions workflows for packages registered to [autoware-index](https://github.com/autowarefoundation/autoware-index).

The primary export is **`validate-package.yaml`** — a reusable workflow that builds and tests a ROS 2 package inside a version-pinned Autoware Core container image (`ghcr.io/autowarefoundation/autoware:core-devel-<ros_distro>-<autoware_version>`).

The same workflow serves two callers:

- **Producer mode** — a community package's own CI calls it to validate "my latest code still builds against pinned Autoware Core X.Y.Z."
- **Sweep mode** — the autoware-index registry's sweep workflows call it to validate "package P at ref R still builds against pinned Autoware Core X.Y.Z."

## Usage

### Producer mode

In your package's `.github/workflows/build-and-test.yaml`:

```yaml
name: build-and-test
on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    strategy:
      fail-fast: false
      matrix:
        autoware_version: ["1.8.0"]
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/validate-package.yaml@main
    with:
      ros_distro: jazzy
      autoware_version: ${{ matrix.autoware_version }}
      package_name: autoware_livox_tag_filter
```

The workflow runs `actions/checkout` on the calling repository; no `package_repository` input needed.

### Sweep mode

Called from the autoware-index registry's sweep workflows. The registry knows the package's URL and the exact ref to test, so both are passed explicitly:

```yaml
jobs:
  sweep:
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/validate-package.yaml@main
    with:
      ros_distro: jazzy
      autoware_version: "1.8.0"
      package_name: autoware_livox_tag_filter
      package_repository: https://github.com/autowarefoundation/autoware_livox_tag_filter
      package_ref: "0.2.1"
```

The workflow exposes `resolved_sha` as an output so the sweep can record the exact sha that was tested (matters for `branch` refs whose tip moves between sweeps).

## Workflow contract

### Inputs

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | — | ROS 2 distribution (e.g. `jazzy`) |
| `autoware_version` | ✓ | — | Autoware Core SemVer (e.g. `1.8.0`); drives the container tag |
| `package_name` | ✓ | — | ROS package name; passed to `colcon --packages-up-to` |
| `package_repository` | | `""` | Git URL to clone. Empty = producer mode (`actions/checkout`) |
| `package_ref` | | `""` | Tag, branch, or sha to check out (sweep mode only) |
| `base_image_stage` | | `core-devel` | Container image stage. Override for universe-dependent packages |
| `build_depends_repos` | | `build_depends.repos` | Path in the package to an optional `.repos` file listing extra build deps |
| `runs-on` | | `'["ubuntu-24.04"]'` | Runner label as a JSON-encoded array (workaround for missing array inputs) |

### Outputs

| Output | Meaning |
|--------|---------|
| `resolved_sha` | The exact commit sha that was checked out, built, and tested |

## Container image convention

The reusable workflow pins the container to:

```
ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<autoware_version>
```

These images are published from [`autowarefoundation/autoware/docker`](https://github.com/autowarefoundation/autoware/tree/main/docker) per release. The `core-devel` stage ships `/opt/autoware/` already populated with compiled `autoware_core` and its build dependencies, so the workflow doesn't fetch or rebuild core itself — it just builds your package on top.

If the requested tag doesn't exist (e.g. `core-devel-jazzy-1.9.0` before `1.9.0` ships), the container pull fails with `manifest unknown`. That's the intended failure signal — the autoware-index registry's tag poller should only register a new `autoware_version` after the corresponding image is published.

## Related repositories

- [`autoware-index`](https://github.com/autowarefoundation/autoware-index) — the registry whose sweep workflows consume this.
- [`autoware-github-actions`](https://github.com/autowarefoundation/autoware-github-actions) — provides the composite actions this workflow chains (`remove-exec-depend`, `get-self-packages`, `colcon-build`, `colcon-test`).
- [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/tree/main/docker) — source of the container images.
