import logging
import xml.etree.ElementTree as ET
from urllib.error import URLError
from urllib.request import urlopen

LIST_URL = "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"


def _fetch_available_releases():
    try:
        with urlopen(LIST_URL, timeout=15) as response:
            payload = response.read()
    except URLError as exc:
        logging.warning("Unable to reach Overture release listing: %s", exc)
        return None

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        logging.warning("Unable to parse Overture release listing: %s", exc)
        return None

    namespace = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
    releases = []

    for prefix in root.findall('s3:CommonPrefixes', namespace):
        prefix_elem = prefix.find('s3:Prefix', namespace)
        if prefix_elem is None or not prefix_elem.text:
            continue
        trimmed = prefix_elem.text.strip('/')
        parts = trimmed.split('/')
        if len(parts) == 2 and parts[0] == 'release':
            releases.append(parts[1])

    if not releases:
        return None
    return sorted(set(releases))


def resolve_overture_version(requested_version: str) -> str:
    releases = _fetch_available_releases()
    if not releases:
        logging.warning("Could not verify available Overture releases; continuing with '%s'.", requested_version)
        return requested_version

    if isinstance(requested_version, str) and requested_version.lower() == 'latest':
        resolved_version = releases[-1]
        logging.info("Resolved overture_version=latest to %s", resolved_version)
        return resolved_version

    if requested_version not in releases:
        rendered = ", ".join(releases[-5:]) if len(releases) > 5 else ", ".join(releases)
        raise ValueError(
            f"Configured overture_version '{requested_version}' was not found in the bucket. "
            f"Available releases: {rendered or 'none'}."
        )

    return requested_version

