# autoware-index-github-actions

Reusable GitHub Actions workflows for packages registered to [autoware-index](https://github.com/autowarefoundation/autoware-index).

Two reusable workflows ship here, each with one calling pattern:

- **`validate-package.yaml`** — *producer mode.* A community package's own CI calls it to validate "my latest code still builds against the current latest Autoware Core release." Includes ccache caching and a clang-tidy job.
- **`sweep-package.yaml`** — *sweep mode.* The autoware-index registry's sweep workflows call it to validate "package P at ref R still builds against the current latest Autoware Core release." Clones the package explicitly, uploads a per-row result artifact for the registry's recorder, and exports `resolved_sha`.

Both pin the container to:

```
ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<autoware_version>
```

`autoware_version` is **optional**. When unset (the default), a `resolve` job invokes the [`latest-autoware-version`](#latest-autoware-version) composite action so callers don't have to bump CI per release. If a caller already knows the version (e.g. a release branch validating against a specific Autoware), passing `autoware_version: "1.8.0"` explicitly skips the action entirely.

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
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/validate-package.yaml@main
    with:
      ros_distro: ${{ matrix.ros_distro }}
      package_name: autoware_livox_tag_filter
      pull-ccache: ${{ github.event_name != 'push' || github.ref != 'refs/heads/main' }}
      push-ccache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
      cache-key-element: build
      concurrency-group: autoware-index-validate-${{ matrix.ros_distro }}-${{ github.event_name == 'push' && 'main' || github.ref }}
```

The workflow runs `actions/checkout` on the calling repository, resolves the Autoware version, builds the named package against that pinned image, tests it, and runs clang-tidy in a follow-up job (artifact `clang-tidy-result-<ros_distro>-<resolved_version>`).

### Sweep mode (`sweep-package.yaml`)

Called from the autoware-index registry's sweep workflows. The registry knows the package's URL and the exact ref to test, so both are passed explicitly:

```yaml
jobs:
  sweep:
    uses: autowarefoundation/autoware-index-github-actions/.github/workflows/sweep-package.yaml@main
    with:
      ros_distro: jazzy
      package_name: autoware_livox_tag_filter
      package_repository: https://github.com/autowarefoundation/autoware_livox_tag_filter
      package_ref: "0.2.1"
```

The job uploads `validate-result-<ros_distro>-<package_name>-<resolved_version>` containing `result.json` (`autoware_version`, `build_outcome`, `test_outcome`, `resolved_sha`, the inputs). The registry's recorder (`scripts/build_envelopes.py` in autoware-index) globs the per-package artifact and reads the resolved version out of `result.json` — don't rename the artifact prefix without bumping the recorder.

`resolved_sha` is also exported as a workflow output for callers that prefer reading it directly.

## Workflow inputs

### `validate-package.yaml` (producer)

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | — | ROS 2 distribution (e.g. `jazzy`) |
| `autoware_version` | | resolved at runtime | Autoware Core SemVer (e.g. `1.8.0`); drives the container tag. Leave unset to auto-resolve. |
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
| `autoware_version` | | resolved at runtime | Autoware Core SemVer; drives the container tag. Leave unset to auto-resolve. |
| `package_name` | ✓ | — | ROS package name; passed to `colcon --packages-up-to` |
| `package_repository` | ✓ | — | Git URL of the package to clone |
| `package_ref` | ✓ | — | Tag, branch, or sha of `package_repository` to check out |
| `base_image_stage` | | `core-devel` | Container image stage |
| `runs-on` | | `'["ubuntu-24.04"]'` | Runner label as a JSON-encoded array |

**Output:** `resolved_sha` — the exact commit SHA that was checked out and built.

## `latest-autoware-version` (composite action)

`.github/actions/latest-autoware-version/action.yaml` returns the SemVer of the freshest [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/releases) release whose `ghcr.io/autowarefoundation/autoware:<base_image_stage>-<ros_distro>-<version>` image is already published on GHCR. Releases ship before images, so the action walks releases newest-first and returns the first SemVer with a pullable image — sliding past the release-vs-image gap.

Both reusables invoke it in their `resolve` job (skipping it entirely when the caller supplied an explicit `autoware_version`). You can also call it directly from your own workflow if you need the version for something else.

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `ros_distro` | ✓ | — | ROS 2 distribution (e.g. `jazzy`) |
| `base_image_stage` | | `core-devel` | Container stage for the existence probe |
| `max_lookback` | | `5` | How many recent autoware releases to scan |
| `github_token` | | `${{ github.token }}` | Used for the releases API to lift the anonymous rate limit |

**Output:** `autoware_version` — the resolved SemVer, e.g. `1.8.0`. Fails loudly if none of the last `max_lookback` releases has a published image.

Direct invocation:

```yaml
- id: latest
  uses: autowarefoundation/autoware-index-github-actions/.github/actions/latest-autoware-version@main
  with:
    ros_distro: jazzy
- run: echo "Using autoware ${{ steps.latest.outputs.autoware_version }}"
```

## Container image convention

These images are published from [`autowarefoundation/autoware/docker`](https://github.com/autowarefoundation/autoware/tree/main/docker) per release. The `core-devel` stage ships `/opt/autoware/` already populated with compiled `autoware_core` and its build dependencies, so neither workflow fetches or rebuilds core itself — they just build your package on top.

The release-vs-image race is handled by `latest-autoware-version`'s walk (see above). If every recent release lacks an image, the action fails loudly with the list of skipped versions; callers can work around it by passing `autoware_version: "..."` explicitly until the image lands.

## Related repositories

- [`autoware-index`](https://github.com/autowarefoundation/autoware-index) — the registry whose sweep workflows consume `sweep-package.yaml`.
- [`autoware-github-actions`](https://github.com/autowarefoundation/autoware-github-actions) — provides the composite actions this workflow chains (`remove-exec-depend`, `get-self-packages`, `colcon-build`, `colcon-test`, `clang-tidy`).
- [`autowarefoundation/autoware`](https://github.com/autowarefoundation/autoware/tree/main/docker) — source of the container images.
