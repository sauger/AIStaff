"""Prepare a relocatable Node + Anthropic Sandbox Runtime bundle.

The resulting directory contains ``bin/node`` and the SRT package. Runtime
code invokes the package entrypoint with the bundled Node binary, so the
installed app does not depend on a user's global npm installation.

Verified Node archives and prepared bundles are reused from ``packaging/cache``
(override with ``STAFFDECK_PACKAGING_CACHE``). See ``packaging/BUILD.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(line_buffering=True)

PACKAGE = "@anthropic-ai/sandbox-runtime@0.0.67"
MANIFEST_DIR = Path(__file__).resolve().parent
PACKAGE_JSON = MANIFEST_DIR / "sandbox-runtime-package.json"
PACKAGE_LOCK = MANIFEST_DIR / "sandbox-runtime-package-lock.json"
NODE_VERSION = os.environ.get("STAFFDECK_NODE_VERSION", "v22.14.0")
PATCH_MARKER = "staffdeck-allow-all-domains-patch-v1"
PATCHED_SHA256 = {
    "sandbox-config.js": "17a9bdd4cce375bb098f9c02eb564cf80806079571d4ff784e2af7d27db446bb",
    "sandbox-manager.js": "dca5176508d6ee31807e2138eea82bcbb6f6e5e40c67aff2ac9958d3bc893b4c",
}
SRT_INTEGRITY = "sha512-4doSyr6KNdc/4zARMXYEawhFu3z6bPQjgKRq3lKp6dbgEYVMv39oaLJ28QsDc7TmLvrLqzHW+VzD2LAXxvnw8A=="
STAMP_NAME = ".staffdeck-sandbox-cache.json"
NODE_DIST_BASES = (
    "https://nodejs.org/dist",
    "https://cdn.npmmirror.com/binaries/node",
    "https://mirrors.aliyun.com/nodejs-release",
)
NODE_SHA256 = {
    "node-v22.14.0-darwin-arm64.tar.gz": "e9404633bc02a5162c5c573b1e2490f5fb44648345d64a958b17e325729a5e42",
    "node-v22.14.0-darwin-x64.tar.gz": "6698587713ab565a94a360e091df9f6d91c8fadda6d00f0cf6526e9b40bed250",
    "node-v22.14.0-linux-x64.tar.gz": "9d942932535988091034dc94cc5f42b6dc8784d6366df3a36c4c9ccb3996f0c2",
    "node-v22.14.0-linux-arm64.tar.gz": "8cf30ff7250f9463b53c18f89c6c606dfda70378215b2c905d0a9a8b08bd45e0",
    "node-v22.14.0-win-arm64.zip": "2d71f5f9b2fffa33baa108c07d74b0d24e0c3dd8f441d567772ae0e3dd4b1a22",
    "node-v22.14.0-win-x64.zip": "55b639295920b219bb2acbcfa00f90393a2789095b7323f79475c9f34795f217",
}


def _apply_allow_all_domains_patch(destination: Path) -> None:
    """Apply the reviewed upstream PR #283 behavior to the bundled dist files."""
    package_root = destination / "node_modules" / "@anthropic-ai" / "sandbox-runtime"
    config_path = package_root / "dist" / "sandbox" / "sandbox-config.js"
    manager_path = package_root / "dist" / "sandbox" / "sandbox-manager.js"
    if not config_path.is_file() or not manager_path.is_file():
        raise SystemExit("SRT bundle layout is unexpected; refusing to patch it.")
    config = config_path.read_text(encoding="utf-8")
    manager = manager_path.read_text(encoding="utf-8")
    if PATCH_MARKER in config and PATCH_MARKER in manager:
        _verify_patched_dist(config_path, manager_path)
        return
    config_anchor = "    allowUnixSockets: z\n"
    config_insert = (
        "    // " + PATCH_MARKER + "\n"
        "    allowAllDomains: z.boolean().optional(),\n"
    )
    manager_anchor = "    // Check allowed domains\n"
    manager_insert = (
        "    // " + PATCH_MARKER + "\n"
        "    if (config.network.allowAllDomains) {\n"
        "        logForDebugging(`Allowed by allowAllDomains: ${host}:${port}`);\n"
        "        return true;\n"
        "    }\n"
    )
    if config.count(config_anchor) != 1 or manager.count(manager_anchor) != 1:
        raise SystemExit("SRT patch anchors changed; refusing an unverified patch.")
    config_path.write_bytes(
        config.replace(config_anchor, config_insert + config_anchor).encode("utf-8")
    )
    manager_path.write_bytes(
        manager.replace(manager_anchor, manager_insert + manager_anchor).encode("utf-8")
    )
    if PATCH_MARKER not in config_path.read_text(encoding="utf-8") or PATCH_MARKER not in manager_path.read_text(encoding="utf-8"):
        raise SystemExit("SRT patch verification failed.")
    _verify_patched_dist(config_path, manager_path)


def _verify_patched_dist(config_path: Path, manager_path: Path) -> None:
    for path in (config_path, manager_path):
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != PATCHED_SHA256[path.name]:
            raise SystemExit(f"Patched SRT dist hash mismatch: {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    bundle_cache = _bundle_cache_dir()
    if _cached_bundle_is_valid(bundle_cache):
        print(f"Reusing cached SRT + Node bundle at {bundle_cache}")
        _copy_bundle(bundle_cache, destination)
        print(f"SRT runtime ready at {destination}")
        return 0
    if bundle_cache.exists():
        print(f"Ignoring invalid sandbox runtime cache {bundle_cache}", file=sys.stderr)

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required to prepare the SRT runtime bundle.")

    destination.mkdir(parents=True, exist_ok=True)
    (destination / "bin").mkdir(parents=True, exist_ok=True)
    _download_node_runtime(destination)
    shutil.copy2(PACKAGE_JSON, destination / "package.json")
    shutil.copy2(PACKAGE_LOCK, destination / "package-lock.json")
    npm_command = [
        npm, "ci", "--prefix", str(destination), "--no-audit", "--no-fund",
        "--omit=dev",
    ]
    npm_cache = _cache_root() / "npm"
    try:
        npm_cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        npm_cache = None
    if npm_cache is not None:
        npm_command.extend(["--cache", str(npm_cache)])
    subprocess.run(npm_command, check=True)
    _verify_srt_integrity(destination)
    _apply_allow_all_domains_patch(destination)
    _publish_bundle_cache(destination, bundle_cache)
    print(f"SRT runtime ready at {destination}")
    return 0


def _cache_root() -> Path:
    override = os.environ.get("STAFFDECK_PACKAGING_CACHE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (MANIFEST_DIR / "cache").resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_archive_spec() -> tuple[str, str, str]:
    """Return ``(filename, suffix, system)`` for the pinned Node runtime."""
    system = platform.system().lower()
    machine = _machine()
    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine)
    if not arch:
        raise SystemExit(f"Unsupported Node architecture: {machine}")
    if system == "darwin":
        target, suffix = f"darwin-{arch}", "tar.gz"
    elif system == "linux":
        target, suffix = f"linux-{arch}", "tar.gz"
    elif system == "windows":
        target, suffix = f"win-{arch}", "zip"
    else:
        raise SystemExit(f"Unsupported Node platform: {system}")
    return f"node-{NODE_VERSION}-{target}.{suffix}", suffix, system


def _expected_node_hash(filename: str) -> str:
    override = os.environ.get("STAFFDECK_NODE_SHA256", "").strip().lower()
    if override:
        return override
    return NODE_SHA256.get(filename, "")


def _node_archive_urls(filename: str) -> list[str]:
    """Official dist first, then China-friendly mirrors. ``STAFFDECK_NODE_DIST_BASE`` is prepended."""
    bases: list[str] = []
    override = os.environ.get("STAFFDECK_NODE_DIST_BASE", "").strip().rstrip("/")
    if override:
        bases.append(override)
    bases.extend(NODE_DIST_BASES)
    urls: list[str] = []
    seen: set[str] = set()
    for base in bases:
        url = f"{base}/{NODE_VERSION}/{filename}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _download_url(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StaffDeck-packaging/1.0", "Accept": "*/*"},
    )
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output, length=1024 * 1024)


def _acquire_node_archive(filename: str, expected_hash: str) -> Path:
    cached = _cache_root() / "node" / filename
    partial = Path(str(cached) + ".part")
    try:
        if cached.is_file():
            if _sha256_file(cached) == expected_hash:
                print(f"Reusing cached Node archive {cached}")
                return cached
            print(
                f"Cached Node archive failed SHA256 validation; refetching {filename}",
                file=sys.stderr,
            )
            cached.unlink()
        cached.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"Node cache unavailable ({exc}); downloading without a durable cache",
            file=sys.stderr,
        )
        temporary = Path(tempfile.mkdtemp(prefix="staffdeck-node-"))
        cached = temporary / filename
        partial = Path(str(cached) + ".part")

    errors: list[str] = []
    for url in _node_archive_urls(filename):
        try:
            print(f"Downloading Node runtime from {url}")
            partial.unlink(missing_ok=True)
            _download_url(url, partial)
            actual_hash = _sha256_file(partial)
            if actual_hash != expected_hash:
                partial.unlink(missing_ok=True)
                errors.append(f"{url}: SHA256 mismatch (got {actual_hash})")
                continue
            partial.replace(cached)
            return cached
        except (OSError, urllib.error.URLError, ValueError) as exc:
            partial.unlink(missing_ok=True)
            errors.append(f"{url}: {exc}")
    raise SystemExit(
        f"Failed to download Node runtime {filename} (expected SHA256 {expected_hash}):\n"
        + "\n".join(f"- {item}" for item in errors)
    )


def _download_node_runtime(destination: Path) -> None:
    filename, suffix, system = _node_archive_spec()
    expected_hash = _expected_node_hash(filename)
    if not expected_hash:
        raise SystemExit(
            f"No trusted SHA256 is configured for {filename}; set STAFFDECK_NODE_SHA256."
        )
    archive = _acquire_node_archive(filename, expected_hash)
    try:
        _extract_node_binary(archive, destination, suffix=suffix, system=system)
    finally:
        _release_temporary_archive(archive)


def _release_temporary_archive(archive: Path) -> None:
    """Delete archives that could not be stored in the durable cache."""
    try:
        retained = (_cache_root() / "node").resolve()
        if archive.resolve().is_relative_to(retained):
            return
    except (OSError, ValueError):
        return
    shutil.rmtree(archive.parent, ignore_errors=True)


@contextlib.contextmanager
def _open_archived_node(archive: Path, suffix: str) -> Iterator[object]:
    if suffix == "tar.gz":
        with tarfile.open(archive, "r:gz") as handle:
            members = [
                member
                for member in handle.getmembers()
                if member.name.endswith("/bin/node") and member.isfile()
            ]
            if len(members) != 1:
                raise SystemExit("Node archive does not contain exactly one bin/node.")
            source = handle.extractfile(members[0])
            if source is None:
                raise SystemExit("Node executable cannot be read from the archive.")
            with source:
                yield source
        return
    with zipfile.ZipFile(archive) as handle:
        members = [
            name
            for name in handle.namelist()
            if name.endswith("/node.exe") and not name.endswith("/")
        ]
        if len(members) != 1:
            raise SystemExit("Node archive does not contain exactly one node.exe.")
        with handle.open(members[0]) as source:
            yield source


def _archived_node_sha256(archive: Path, suffix: str) -> str:
    digest = hashlib.sha256()
    with _open_archived_node(archive, suffix) as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_node_binary(
    archive: Path,
    destination: Path,
    *,
    suffix: str,
    system: str,
) -> None:
    target_node = destination / _node_binary_relative(system)
    target_node.parent.mkdir(parents=True, exist_ok=True)
    with _open_archived_node(archive, suffix) as source, target_node.open("wb") as output:
        shutil.copyfileobj(source, output)
    if system != "windows":
        target_node.chmod(target_node.stat().st_mode | 0o111)


def _bundle_cache_key() -> str:
    filename, _, _system = _node_archive_spec()
    payload = {
        "lock_sha256": hashlib.sha256(PACKAGE_LOCK.read_bytes()).hexdigest(),
        "machine": _machine(),
        "node_filename": filename,
        "node_sha256": _expected_node_hash(filename),
        "node_version": NODE_VERSION,
        "package_sha256": hashlib.sha256(PACKAGE_JSON.read_bytes()).hexdigest(),
        "patch": PATCH_MARKER,
        "patched_sha256": PATCHED_SHA256,
        "platform": platform.system().lower(),
        "srt_integrity": SRT_INTEGRITY,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _bundle_cache_dir() -> Path:
    return _cache_root() / "sandbox-runtime" / _bundle_cache_key()


def _node_binary_relative(system: str) -> str:
    return "bin/node.exe" if system == "windows" else "bin/node"


def _ensure_node_executable(bundle: Path) -> None:
    _filename, _suffix, system = _node_archive_spec()
    if system == "windows":
        return
    binary = bundle / _node_binary_relative(system)
    if binary.is_file() and not binary.is_symlink():
        binary.chmod(binary.stat().st_mode | 0o111)


def _bundle_manifest(bundle: Path) -> dict[str, str]:
    """Content manifest for every shipped file, excluding the cache stamp."""
    manifest: dict[str, str] = {}
    for path in bundle.rglob("*"):
        if path.name == STAMP_NAME or not (path.is_symlink() or path.is_file()):
            continue
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            manifest[relative] = "symlink:" + os.readlink(path)
        else:
            manifest[relative] = _sha256_file(path)
    return manifest


def _write_bundle_stamp(bundle: Path) -> None:
    filename, _, _system = _node_archive_spec()
    stamp = {
        "files": _bundle_manifest(bundle),
        "key": _bundle_cache_key(),
        "node_filename": filename,
        "node_sha256": _expected_node_hash(filename),
    }
    (bundle / STAMP_NAME).write_text(
        json.dumps(stamp, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _installed_node_matches_archive(bundle: Path) -> bool:
    """Require bin/node to be the executable inside the hash-pinned archive."""
    filename, suffix, system = _node_archive_spec()
    expected_hash = _expected_node_hash(filename)
    archive = _cache_root() / "node" / filename
    binary = bundle / _node_binary_relative(system)
    if (
        not expected_hash
        or not archive.is_file()
        or binary.is_symlink()
        or not binary.is_file()
        or _sha256_file(archive) != expected_hash
    ):
        return False
    return _sha256_file(binary) == _archived_node_sha256(archive, suffix)


def _cached_bundle_is_valid(cache_dir: Path) -> bool:
    stamp_path = cache_dir / STAMP_NAME
    if not stamp_path.is_file():
        return False
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        files = stamp.get("files") if isinstance(stamp, dict) else None
        if not isinstance(stamp, dict) or stamp.get("key") != _bundle_cache_key():
            return False
        if not isinstance(files, dict) or files != _bundle_manifest(cache_dir):
            return False
        if not _installed_node_matches_archive(cache_dir):
            return False
        _verify_srt_integrity(cache_dir)
        package_root = cache_dir / "node_modules" / "@anthropic-ai" / "sandbox-runtime"
        _verify_patched_dist(
            package_root / "dist" / "sandbox" / "sandbox-config.js",
            package_root / "dist" / "sandbox" / "sandbox-manager.js",
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError, SystemExit):
        return False
    return True


def _copy_bundle(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(STAMP_NAME),
    )
    _ensure_node_executable(destination)


def _publish_bundle_cache(source: Path, cache_dir: Path) -> None:
    staging = cache_dir.with_name(cache_dir.name + ".partial")
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            staging,
            symlinks=True,
            ignore=shutil.ignore_patterns(STAMP_NAME),
        )
        _write_bundle_stamp(staging)
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        staging.rename(cache_dir)
    except OSError as exc:
        print(f"Could not persist SRT bundle cache: {exc}", file=sys.stderr)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return
    print(f"Cached SRT + Node bundle at {cache_dir}")


def _machine() -> str:
    machine = platform.machine() or os.environ.get("PROCESSOR_ARCHITECTURE", "")
    if machine:
        return machine.lower()
    if platform.system().lower() == "windows" and sys.maxsize > 2**32:
        return "amd64"
    return machine.lower()


def _verify_srt_integrity(destination: Path) -> None:
    lock_path = destination / "package-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        trusted = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
        packages = _normalized_lock_packages(lock["packages"])
        trusted_packages = _normalized_lock_packages(trusted["packages"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SystemExit("SRT package lock is missing or malformed; refusing the bundle.") from exc
    if packages != trusted_packages:
        raise SystemExit("SRT dependency graph does not match the reviewed lockfile.")
    for relative, package in trusted_packages.items():
        if not relative:
            continue
        try:
            installed = json.loads(
                (destination / relative / "package.json").read_text(encoding="utf-8")
            )
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"SRT dependency is missing: {relative}") from exc
        if installed.get("version") != package.get("version"):
            raise SystemExit(f"SRT dependency version mismatch: {relative}")


def _normalized_lock_packages(packages: object) -> dict[str, object]:
    if not isinstance(packages, dict):
        raise TypeError("packages must be an object")
    normalized: dict[str, object] = {}
    for raw_key, value in packages.items():
        key = str(raw_key).replace("\\", "/")
        marker = "node_modules/"
        if marker in key:
            key = marker + key.rsplit(marker, 1)[1]
        elif key:
            continue
        normalized[key] = value
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
