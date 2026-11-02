from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "fetch_sandbox_runtime.py"
SPEC = importlib.util.spec_from_file_location("staffdeck_fetch_sandbox_runtime", SCRIPT)
assert SPEC and SPEC.loader
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


def test_reviewed_node_archives_have_built_in_hashes() -> None:
    expected = {
        "darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64",
        "win-arm64", "win-x64",
    }

    assert expected == {
        filename.removeprefix("node-v22.14.0-").removesuffix(".tar.gz").removesuffix(".zip")
        for filename in fetch.NODE_SHA256
    }


def test_windows_machine_falls_back_to_process_bitness(monkeypatch) -> None:
    monkeypatch.setattr(fetch.platform, "machine", lambda: "")
    monkeypatch.setattr(fetch.platform, "system", lambda: "Windows")
    monkeypatch.delenv("PROCESSOR_ARCHITECTURE", raising=False)
    monkeypatch.setattr(fetch.sys, "maxsize", 2**63 - 1)

    assert fetch._machine() == "amd64"


def test_srt_lock_integrity_is_enforced(tmp_path: Path) -> None:
    lock = json.loads(fetch.PACKAGE_LOCK.read_text(encoding="utf-8"))
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    for relative, package in lock["packages"].items():
        if not relative:
            continue
        package_root = tmp_path / relative
        package_root.mkdir(parents=True)
        (package_root / "package.json").write_text(
            json.dumps({"version": package["version"]}), encoding="utf-8"
        )
    fetch._verify_srt_integrity(tmp_path)

    lock["packages"]["node_modules/zod"]["integrity"] = "sha512-changed"
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(SystemExit, match="dependency graph"):
        fetch._verify_srt_integrity(tmp_path)


def _fake_node_archive(suffix: str) -> bytes:
    payload = b"fake-node-binary"
    buffer = io.BytesIO()
    if suffix == "tar.gz":
        with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
            info = tarfile.TarInfo("node-custom/bin/node")
            info.size = len(payload)
            info.mode = 0o755
            handle.addfile(info, io.BytesIO(payload))
    else:
        with zipfile.ZipFile(buffer, "w") as handle:
            handle.writestr("node-custom/node.exe", payload)
    return buffer.getvalue()


def _write_locked_packages(root: Path) -> None:
    lock = json.loads(fetch.PACKAGE_LOCK.read_text(encoding="utf-8"))
    for relative, package in lock["packages"].items():
        if not relative:
            continue
        package_root = root / relative
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "package.json").write_text(
            json.dumps({"version": package["version"]}),
            encoding="utf-8",
        )


def test_packaging_cache_path_can_be_overridden(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "durable"
    monkeypatch.setenv("STAFFDECK_PACKAGING_CACHE", str(target))

    assert fetch._cache_root() == target.resolve()


def test_node_filename_includes_version(monkeypatch) -> None:
    monkeypatch.setattr(fetch, "NODE_VERSION", "v1.2.3")
    monkeypatch.setattr(fetch.platform, "system", lambda: "Linux")
    monkeypatch.setattr(fetch.platform, "machine", lambda: "x86_64")

    filename, suffix, system = fetch._node_archive_spec()

    assert filename == "node-v1.2.3-linux-x64.tar.gz"
    assert suffix == "tar.gz"
    assert system == "linux"


def test_node_archive_urls_try_official_then_china_mirrors(monkeypatch) -> None:
    monkeypatch.delenv("STAFFDECK_NODE_DIST_BASE", raising=False)
    monkeypatch.setattr(fetch, "NODE_VERSION", "v22.14.0")
    filename = "node-v22.14.0-linux-x64.tar.gz"

    urls = fetch._node_archive_urls(filename)

    assert urls[0] == f"https://nodejs.org/dist/v22.14.0/{filename}"
    assert f"https://cdn.npmmirror.com/binaries/node/v22.14.0/{filename}" in urls
    assert f"https://mirrors.aliyun.com/nodejs-release/v22.14.0/{filename}" in urls
    monkeypatch.setenv("STAFFDECK_NODE_DIST_BASE", "https://cdn.npmmirror.com/binaries/node/")
    overridden = fetch._node_archive_urls(filename)
    assert overridden[0] == f"https://cdn.npmmirror.com/binaries/node/v22.14.0/{filename}"
    assert overridden[1] == urls[0]
    assert overridden.count(overridden[0]) == 1


def test_matching_node_archive_is_not_downloaded_again(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fetch, "_cache_root", lambda: tmp_path)
    filename, suffix, system = fetch._node_archive_spec()
    archive = _fake_node_archive(suffix)
    cached = tmp_path / "node" / filename
    cached.parent.mkdir(parents=True)
    cached.write_bytes(archive)
    monkeypatch.setenv("STAFFDECK_NODE_SHA256", hashlib.sha256(archive).hexdigest())
    monkeypatch.setattr(
        fetch,
        "_download_url",
        lambda _url, _dest: (_ for _ in ()).throw(AssertionError("archive was downloaded")),
    )
    destination = tmp_path / "bundle"

    fetch._download_node_runtime(destination)
    fetch._download_node_runtime(tmp_path / "bundle-again")

    binary = destination / ("bin/node.exe" if system == "windows" else "bin/node")
    assert binary.read_bytes() == b"fake-node-binary"
    assert cached.read_bytes() == archive


def test_node_archive_refetches_when_cached_hash_does_not_match(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(fetch, "_cache_root", lambda: tmp_path)
    filename = "node-v22.14.0-linux-x64.tar.gz"
    stale = tmp_path / "node" / filename
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"corrupt-archive")
    payload = b"good-archive"
    expected = hashlib.sha256(payload).hexdigest()
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        calls.append(url)
        if "nodejs.org" in url:
            dest.write_bytes(b"wrong-bytes")
            return
        dest.write_bytes(payload)

    monkeypatch.setattr(fetch, "_download_url", fake_download)

    restored = fetch._acquire_node_archive(filename, expected)

    assert restored.read_bytes() == payload
    assert any("nodejs.org" in url for url in calls)
    assert any("npmmirror.com" in url for url in calls)
    calls.clear()
    assert fetch._acquire_node_archive(filename, expected) == restored
    assert calls == []


def test_node_download_falls_back_when_official_dist_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fetch, "_cache_root", lambda: tmp_path)
    filename = "node-v22.14.0-linux-x64.tar.gz"
    payload = b"mirror-archive"
    expected = hashlib.sha256(payload).hexdigest()
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        calls.append(url)
        if "nodejs.org" in url:
            raise TimeoutError("timed out")
        dest.write_bytes(payload)

    monkeypatch.setattr(fetch, "_download_url", fake_download)

    restored = fetch._acquire_node_archive(filename, expected)

    assert restored.read_bytes() == payload
    assert "nodejs.org" in calls[0]
    assert "npmmirror.com" in calls[1]


def test_changed_node_version_does_not_reuse_another_archive(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(fetch, "_cache_root", lambda: tmp_path)
    old = tmp_path / "node" / "node-v22.14.0-linux-x64.tar.gz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old-version")
    payload = b"new-version"
    expected = hashlib.sha256(payload).hexdigest()
    new_name = "node-v22.15.0-linux-x64.tar.gz"

    def fake_download(url: str, dest: Path) -> None:
        assert url.endswith(new_name)
        dest.write_bytes(payload)

    monkeypatch.setattr(fetch, "_download_url", fake_download)

    restored = fetch._acquire_node_archive(new_name, expected)

    assert restored.name == new_name
    assert restored.read_bytes() == payload
    assert old.read_bytes() == b"old-version"


def test_bundle_cache_key_changes_with_node_version_and_lockfile(
    tmp_path: Path, monkeypatch
) -> None:
    original_version = fetch.NODE_VERSION
    first = fetch._bundle_cache_key()
    monkeypatch.setattr(fetch, "NODE_VERSION", "v99.0.0")
    assert fetch._bundle_cache_key() != first

    monkeypatch.setattr(fetch, "NODE_VERSION", original_version)
    assert fetch._bundle_cache_key() == first
    replacement = tmp_path / "package-lock.json"
    replacement.write_bytes(fetch.PACKAGE_LOCK.read_bytes() + b"\n")
    monkeypatch.setattr(fetch, "PACKAGE_LOCK", replacement)
    assert fetch._bundle_cache_key() != first


def test_second_build_reuses_bundle_without_downloading_node(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setattr(fetch, "_cache_root", lambda: cache)
    _filename, suffix, system = fetch._node_archive_spec()
    archive = _fake_node_archive(suffix)
    monkeypatch.setenv("STAFFDECK_NODE_SHA256", hashlib.sha256(archive).hexdigest())
    downloads = {"count": 0}
    npm_runs = {"count": 0}

    def fake_download(_url: str, dest: Path) -> None:
        downloads["count"] += 1
        dest.write_bytes(archive)

    config = f"// {fetch.PATCH_MARKER}\nconfig\n".encode()
    manager = f"// {fetch.PATCH_MARKER}\nmanager\n".encode()
    monkeypatch.setitem(
        fetch.PATCHED_SHA256,
        "sandbox-config.js",
        hashlib.sha256(config).hexdigest(),
    )
    monkeypatch.setitem(
        fetch.PATCHED_SHA256,
        "sandbox-manager.js",
        hashlib.sha256(manager).hexdigest(),
    )
    monkeypatch.setattr(fetch, "_download_url", fake_download)
    monkeypatch.setattr(fetch.shutil, "which", lambda _name: "npm")

    def fake_npm(command, check):
        del check
        npm_runs["count"] += 1
        prefix = Path(command[command.index("--prefix") + 1])
        _write_locked_packages(prefix)
        dist = prefix / "node_modules/@anthropic-ai/sandbox-runtime/dist/sandbox"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "sandbox-config.js").write_bytes(config)
        (dist / "sandbox-manager.js").write_bytes(manager)
        cli = dist.parent / "cli.js"
        cli.write_text("console.log('srt')\n", encoding="utf-8")
        link = prefix / "node_modules/.bin/srt"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("../@anthropic-ai/sandbox-runtime/dist/cli.js")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(fetch.subprocess, "run", fake_npm)
    destination = tmp_path / "sandbox_runtime"
    monkeypatch.setattr(sys, "argv", ["fetch_sandbox_runtime.py", str(destination)])

    assert fetch.main() == 0
    assert downloads["count"] == 1
    assert npm_runs["count"] == 1
    binary_name = "node.exe" if system == "windows" else "node"
    assert (destination / "bin" / binary_name).read_bytes() == b"fake-node-binary"
    assert not (destination / fetch.STAMP_NAME).exists()

    shutil.rmtree(destination)
    assert fetch.main() == 0
    assert downloads["count"] == 1
    assert npm_runs["count"] == 1
    assert (destination / "bin" / binary_name).read_bytes() == b"fake-node-binary"

    cached_root = cache / "sandbox-runtime" / fetch._bundle_cache_key()
    cli = cached_root / "node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js"
    cli.write_text("console.log('tampered')\n", encoding="utf-8")
    shutil.rmtree(destination)
    assert fetch.main() == 0
    assert downloads["count"] == 1
    assert npm_runs["count"] == 2
    assert (destination / "bin" / binary_name).read_bytes() == b"fake-node-binary"
    assert "tampered" not in cli.read_text(encoding="utf-8")

    cached_root = cache / "sandbox-runtime" / fetch._bundle_cache_key()
    stamp_path = cached_root / fetch.STAMP_NAME
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    node_key = f"bin/{binary_name}"
    stamp["files"][node_key] = hashlib.sha256(b"evil-node").hexdigest()
    (cached_root / "bin" / binary_name).write_bytes(b"evil-node")
    stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    shutil.rmtree(destination)
    assert fetch.main() == 0
    assert downloads["count"] == 1
    assert npm_runs["count"] == 3
    assert (destination / "bin" / binary_name).read_bytes() == b"fake-node-binary"


def test_invalid_node_dist_base_falls_through_to_later_urls(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(fetch, "_cache_root", lambda: tmp_path)
    monkeypatch.setenv("STAFFDECK_NODE_DIST_BASE", "not a url")
    payload = b"from-official"
    expected = hashlib.sha256(payload).hexdigest()

    def fake_download(url: str, dest: Path) -> None:
        if url.startswith("not a url"):
            raise ValueError("unknown url type")
        dest.write_bytes(payload)

    monkeypatch.setattr(fetch, "_download_url", fake_download)

    restored = fetch._acquire_node_archive("node-v22.14.0-linux-x64.tar.gz", expected)

    assert restored.read_bytes() == payload


def test_temporary_archive_is_removed_when_cache_is_unwritable(
    tmp_path: Path, monkeypatch
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fetch, "_cache_root", lambda: blocker)
    _filename, suffix, system = fetch._node_archive_spec()
    archive = _fake_node_archive(suffix)
    monkeypatch.setenv("STAFFDECK_NODE_SHA256", hashlib.sha256(archive).hexdigest())
    monkeypatch.setattr(fetch, "_download_url", lambda _url, dest: dest.write_bytes(archive))
    created: list[Path] = []
    real_mkdtemp = fetch.tempfile.mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(fetch.tempfile, "mkdtemp", spy_mkdtemp)

    fetch._download_node_runtime(tmp_path / "out")

    binary_name = "node.exe" if system == "windows" else "node"
    assert created and not created[0].exists()
    assert (tmp_path / "out" / "bin" / binary_name).read_bytes() == b"fake-node-binary"
