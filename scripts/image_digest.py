"""Resolves a ghcr.io/jameswoolfenden/pan tag to its content digest, so `make deploy` pins the NAS
to the exact image CI published - a tag can be re-pushed, a digest can't. Exits non-zero with an
explanation if the image doesn't exist (yet), before anything on the NAS is touched."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

REPO = "jameswoolfenden/pan"
ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def main(tag: str) -> None:
    missing = (
        f"No image ghcr.io/{REPO}:{tag} - is the commit pushed, has CI finished "
        "(gh run list), and is the package public?"
    )
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{REPO}:pull"
        ) as resp:
            token = json.load(resp)["token"]
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{REPO}/manifests/{tag}",
            method="HEAD",
            headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
        )
        with urllib.request.urlopen(request) as resp:
            print(resp.headers["Docker-Content-Digest"])
    except urllib.error.HTTPError as e:
        sys.exit(f"{missing} (HTTP {e.code})")


if __name__ == "__main__":
    main(sys.argv[1])
