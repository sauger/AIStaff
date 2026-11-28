# Application update reminder

StaffDeck desktop builds perform a best-effort check for newer GitHub releases after a user
signs in and completes the first-run guides. The feature only displays a reminder; it never
downloads or installs an update.

## Runtime behavior

`GET /api/app/version` returns the current application version and the newest compatible release
from `https://github.com/OpenBMB/StaffDeck/releases.atom`. A stable build ignores prereleases. A
prerelease build may advance to a newer prerelease or a stable release.

Successful checks are cached for six hours and failed checks for fifteen minutes. Network,
parsing, and invalid-version failures are reported as `check_succeeded=false` and do not affect
normal application use.

The frontend waits until both first-run guides are complete. It then shows a 30-second toast only
when an update exists. The key below records the announced release in that browser, so refreshing
does not repeat the same version:

```text
staffdeck_update_reminded_version
```

The release URL is accepted only when it is an HTTPS GitHub URL under the StaffDeck release-tag
path. Packaged macOS builds hand external links from the embedded StaffDeck window to the system
browser. Other platforms already display StaffDeck in the system browser.

Update comparison compatibility is guaranteed from the `0.2.0` stable release onward. Historical
`0.12-beta.*` development builds predate that version line and are not supported as update-check
starting points. Stable builds ignore all prerelease tags, so those historical tags cannot affect
updates between supported stable versions such as `0.2.0` and `0.2.1`.

## Enablement

Packaged PyInstaller applications enable update checks by default. Source and private deployments
disable them by default to avoid unexpected outbound traffic. They can opt in or out explicitly:

```text
STAFFDECK_UPDATE_CHECK=true
STAFFDECK_UPDATE_CHECK=false
```

`STAFFDECK_VERSION` overrides the current version for development and deployment testing.

## Git build stamps

Production deploys stamp the running build as `<release>-<shortsha>`, for example
`0.1.15-769ecd2`. That value is what check-update shows as the current version. It comes from
`STAFFDECK_VERSION` when set, otherwise from the bundled `staffdeck-version.txt` written at
package time, otherwise `0.1.0`.

`packaging/ultrarag.spec` writes the `VERSION` given to the platform build (the release tag, or
the workflow-dispatch input) into `staffdeck-version.txt`. A web deploy that is already on a
commit sets `STAFFDECK_VERSION` to `0.1.15-` plus that commit's short sha.

The suffix is a git build id. It is not a PEP 440 pre-release, and `packaging.version.Version`
rejects `0.1.15-769ecd2`. The checker therefore parses `x.y.z` plus a 7–40 character hex build id
itself. Two stamps are the same release when the release numbers match and the build ids match,
ignoring a leading `v` and hex case. The same stamp does not report an update.

Plain GitHub tags such as `v0.5.6` and `v1.0.0-preview` are a different release line. They are not
offered as updates for a git-stamped build, including when the feed contains no stamp newer than
the one that is running. A higher `x.y.z-<shortsha>` tag is an update. A different build id of the
same `x.y.z` is an update only when the running sha is in the feed and the other tag was published
at a strictly later instant. Hex order and equal timestamps do not count. Plain semver builds
ignore git-stamped tags and keep the previous GitHub release comparison.

## Build version

`packaging/ultrarag.spec` reads `VERSION`, validates it, and writes
`packaging/build/staffdeck-version.txt`. PyInstaller bundles that file with the application.
Runtime lookup supports the PyInstaller extraction directory, an executable-adjacent resource,
and macOS `Contents/Resources`.

Example:

```bash
VERSION=v0.2.0 bash packaging/build_macos.sh
```

## Manual acceptance

1. Start a packaged build, or source deployment with checks enabled, using a version older than
   the latest GitHub release.
2. Sign in and complete or close both first-run guides.
3. Confirm the toast identifies the current and latest versions and links to the matching trusted
   GitHub release.
4. Close the toast and refresh. Confirm the same release is not shown again.
5. Test while offline and confirm the rest of StaffDeck remains usable.
6. Test a stable current version against a feed containing only prereleases and confirm no update
   is offered.
