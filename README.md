# autoware-index-github-actions

Reusable GitHub Actions workflows for packages registered to [autoware-index](https://github.com/autowarefoundation/autoware-index).

One reusable workflow ships here:

- **`validate-package.yaml`**: *producer mode.* A community package's own CI calls it to validate "my latest code still builds against the current latest Autoware Core release." Includes ccache caching and a clang-tidy job. A repository hosting several registered packages calls it once per package via a matrix (see the example below).

The registry-side counterpart, `sweep-repository.yaml`, used to live here too. It now lives in [autoware-index](https://github.com/autowarefoundation/autoware-index) alongside the recorder that parses its output, so the two halves of that contract change together.

It pins the container to:

```
ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<autoware_version>
```

`autoware_version` is **optional**. When unset (the default), a `resolve` job invokes the [`latest-autoware-version`](#latest-autoware-version) composite action so callers don't have to bump CI per release. If a caller already knows the version (e.g. a release branch validating against a specific Autoware), passing `autoware_version: "1.8.0"` explicitly skips the action entirely.

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
| `package_name` | ✓ | - | ROS package name; passed to `colcon --packages-up-to` |
| `base_image_stage` | | `core-devel` | Container image stage |
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

## Container image convention

These images are published from [`autowarefoundation/autoware/docker`](https://github.com/autowarefoundation/autoware/tree/main/docker) per release. The `core-devel` stage ships `/opt/autoware/` already populated with compiled `autoware_core` and its build dependencies, so the workflow never fetches or rebuilds core itself; it just builds your package on top.

The release-vs-image race is handled by `latest-autoware-version`'s walk (see above). If every recent release lacks an image, the action fails loudly with the list of skipped versions; callers can work around it by passing `autoware_version: "..."` explicitly until the image lands.

## Related repositories

- [`autoware-index`](https://github.com/autowarefoundation/autoware-index): the registry. Its sweep uses the `latest-autoware-version` action from this repo and hosts its own `sweep-repository.yaml`.
- [`autoware-github-actions`](https://github.com/autowarefoundation/autoware-github-actions) provides the composite actions `validate-package.yaml` chains (`remove-exec-depend`, `get-self-packages`, `colcon-build`, `colcon-test`, `clang-tidy`).
- [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/tree/main/docker): where the container images are built.
