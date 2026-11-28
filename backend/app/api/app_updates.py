from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from app.version import app_version, update_check_enabled

router = APIRouter(prefix="/api/app", tags=["app"])

RELEASES_FEED_URL = "https://github.com/OpenBMB/StaffDeck/releases.atom"
RELEASES_PAGE_URL = "https://github.com/OpenBMB/StaffDeck/releases"
RELEASE_TAG_PATH = "/OpenBMB/StaffDeck/releases/tag/"
SUCCESS_CACHE_SECONDS = 6 * 60 * 60
FAILURE_CACHE_SECONDS = 15 * 60

_cache_lock = threading.Lock()


class AppVersionRead(BaseModel):
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str = RELEASES_PAGE_URL
    release_name: str | None = None
    published_at: str | None = None
    check_enabled: bool = True
    check_succeeded: bool = True


_cached_result: tuple[float, AppVersionRead] | None = None


@dataclass(frozen=True)
class ReleaseEntry:
    version: str
    parsed_version: Version
    name: str
    url: str
    published_at: str | None
    build_id: str | None = None


# Deployed builds are stamped `0.1.15-<shortsha>`. The suffix is a git build id,
# not a PEP 440 pre-release. `packaging.version.Version` rejects that form.
_BUILD_ID_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


@dataclass(frozen=True)
class ParsedStamp:
    version: Version
    build_id: str | None


def _parse_stamp(value: str) -> ParsedStamp | None:
    normalized = value.strip()
    if normalized[:1].lower() == "v":
        normalized = normalized[1:]
    comparable = normalized.partition("+")[0]
    if not comparable:
        return None
    release, separator, build = comparable.rpartition("-")
    if separator and _BUILD_ID_RE.fullmatch(build):
        try:
            version = Version(release)
        except InvalidVersion:
            version = None
        if version is not None and _is_plain_release(version):
            return ParsedStamp(version=version, build_id=build.lower())
    try:
        version = Version(comparable)
    except InvalidVersion:
        return None
    return ParsedStamp(version=version, build_id=None)


def _is_plain_release(version: Version) -> bool:
    return not (
        version.is_prerelease
        or version.is_devrelease
        or version.is_postrelease
        or version.local
        or version.epoch
    )


def _published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_newer(latest: str, current: str) -> bool:
    latest_stamp = _parse_stamp(latest)
    current_stamp = _parse_stamp(current)
    if latest_stamp is None or current_stamp is None:
        return False
    # Plain tags such as v0.5.6 and v1.0.0-preview are a different release line
    # from a git-stamped build. They must not keep prompting on the latest stamp.
    if current_stamp.build_id and not latest_stamp.build_id:
        return False
    if latest_stamp.version != current_stamp.version:
        return latest_stamp.version > current_stamp.version
    # Same x.y.z build ids are not ordered by hex. A different sha is newer only
    # when the feed shows it was published strictly later than the running sha.
    return False


def _release_tag(entry: ElementTree.Element, namespace: dict[str, str]) -> tuple[str, str]:
    link = entry.find("atom:link[@rel='alternate']", namespace)
    release_url = link.get("href", "").strip() if link is not None else ""
    if release_url:
        parsed_url = urlsplit(release_url)
        path = unquote(parsed_url.path)
        if (
            parsed_url.scheme == "https"
            and parsed_url.netloc == "github.com"
            and path.startswith(RELEASE_TAG_PATH)
        ):
            tag = path.removeprefix(RELEASE_TAG_PATH).strip("/")
            if _parse_stamp(tag) is not None:
                return tag, release_url

    entry_id = (entry.findtext("atom:id", default="", namespaces=namespace) or "").strip()
    tag = unquote(entry_id.rsplit("/", 1)[-1]) if "/" in entry_id else ""
    if _parse_stamp(tag) is not None:
        return tag, RELEASES_PAGE_URL
    return "", RELEASES_PAGE_URL


def _parse_release_feed(content: bytes, current: str) -> ReleaseEntry | None:
    root = ElementTree.fromstring(content)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    current_stamp = _parse_stamp(current)
    if current_stamp is None:
        return None
    releases: list[ReleaseEntry] = []
    for entry in root.findall("atom:entry", namespace):
        tag, release_url = _release_tag(entry, namespace)
        stamp = _parse_stamp(tag)
        if stamp is None:
            continue
        if current_stamp.build_id:
            if not stamp.build_id:
                continue
        elif stamp.build_id:
            # Git stamps used to be invalid PEP 440 and were ignored. Keep plain
            # and prerelease builds on the semver line instead of ranking x.y.z-<sha>.
            continue
        elif not current_stamp.version.is_prerelease and stamp.version.is_prerelease:
            # Stable update guarantees start at 0.2.0. Stable builds never consider
            # older prerelease tags, including the historical 0.12-beta release line.
            continue
        title = (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip()
        display = tag[1:] if tag[:1].lower() == "v" else tag
        releases.append(
            ReleaseEntry(
                version=display,
                parsed_version=stamp.version,
                name=title or tag,
                url=release_url,
                published_at=entry.findtext("atom:updated", default=None, namespaces=namespace),
                build_id=stamp.build_id,
            )
        )
    if current_stamp.build_id:
        return _select_newer_build(releases, current_stamp)
    return max(releases, key=lambda item: item.parsed_version, default=None)


def _select_newer_build(releases: list[ReleaseEntry], current: ParsedStamp) -> ReleaseEntry | None:
    """Return a git-stamped release only when it is strictly newer than `current`."""
    higher = [
        item
        for item in releases
        if item.build_id and item.parsed_version > current.version
    ]
    if higher:
        return max(higher, key=_release_sort_key)
    same = [
        item
        for item in releases
        if item.build_id and item.parsed_version == current.version
    ]
    running = next((item for item in same if item.build_id == current.build_id), None)
    # A sha that is not in the feed is the deployed build. Another sha of the
    # same x.y.z must not be offered as an update, or a missing tag looks like
    # a downgrade.
    if running is None:
        return None
    running_at = _published_at(running.published_at)
    if running_at is None:
        return None
    later: list[ReleaseEntry] = []
    for item in same:
        if item.build_id == current.build_id:
            continue
        published = _published_at(item.published_at)
        if published is not None and published > running_at:
            later.append(item)
    if not later:
        return None
    return max(later, key=_release_sort_key)


def _release_sort_key(item: ReleaseEntry) -> tuple[Version, datetime]:
    published = _published_at(item.published_at) or datetime.min.replace(tzinfo=UTC)
    return (item.parsed_version, published)


def _fetch_version() -> AppVersionRead:
    current = app_version()
    if not update_check_enabled():
        return AppVersionRead(
            current_version=current,
            check_enabled=False,
            check_succeeded=False,
        )
    try:
        response = httpx.get(
            RELEASES_FEED_URL,
            headers={"Accept": "application/atom+xml", "User-Agent": "StaffDeck"},
            timeout=4.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        release = _parse_release_feed(response.content, current)
        if release is None:
            # No strictly newer git-stamped release means this build is current.
            # Plain semver failures stay failed so a broken feed is not "up to date".
            current_stamp = _parse_stamp(current)
            if current_stamp is not None and current_stamp.build_id:
                return AppVersionRead(
                    current_version=current,
                    latest_version=current,
                    update_available=False,
                    check_succeeded=True,
                )
            return AppVersionRead(current_version=current, check_succeeded=False)
        current_stamp = _parse_stamp(current)
        # _select_newer_build only returns a stamp that is strictly newer, including
        # a later-published build of the same x.y.z. _is_newer cannot see publish time.
        update_available = (
            True
            if current_stamp is not None and current_stamp.build_id
            else _is_newer(release.version, current)
        )
        return AppVersionRead(
            current_version=current,
            latest_version=release.version,
            update_available=update_available,
            release_url=release.url,
            release_name=release.name,
            published_at=release.published_at,
        )
    except (httpx.HTTPError, ElementTree.ParseError, ValueError, TypeError):
        return AppVersionRead(current_version=current, check_succeeded=False)


@router.get("/version", response_model=AppVersionRead)
def get_app_version() -> AppVersionRead:
    global _cached_result
    now = time.monotonic()
    with _cache_lock:
        if _cached_result is not None and _cached_result[0] > now:
            return _cached_result[1]
        result = _fetch_version()
        ttl = SUCCESS_CACHE_SECONDS if result.check_succeeded else FAILURE_CACHE_SECONDS
        _cached_result = (now + ttl, result)
        return result
