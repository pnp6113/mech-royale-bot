"""Check the vendored reference trees against their recorded Git blobs."""

from hashlib import sha1, sha256
import json
from pathlib import Path
import re


REFERENCE = Path(__file__).resolve().parents[1] / "reference"


def main():
    pins = json.loads((REFERENCE / "SOURCES.json").read_text(encoding="utf-8"))
    for variant, pin in pins.items():
        directory = REFERENCE / variant
        expected = pin["git_blobs"]
        paths = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
        if paths != expected.keys():
            raise SystemExit(f"{variant}: source list differs: {paths ^ expected.keys()}")
        for path, digest in expected.items():
            data = (directory / path).read_bytes()
            actual = sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if actual != digest:
                raise SystemExit(f"{variant}: Git blob mismatch: {path}")
        print(f"{variant}: {len(paths)} verified files at {pin['commit']}")

    kanyaraasi = REFERENCE / "kanyaraasi"
    readme = (kanyaraasi / "README.md").read_text(encoding="utf-8")
    runtime_files = re.findall(r"^- `([^`]+)`: `([a-f0-9]{64})`$", readme, re.MULTILINE)
    if len(runtime_files) != 6:
        raise SystemExit("Kanyaraasi runtime checksum list changed")
    for path, digest in runtime_files:
        if sha256((kanyaraasi / path).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Kanyaraasi runtime checksum mismatch: {path}")
    if (kanyaraasi / "THIRD_PARTY_LICENSES").read_bytes() != (REFERENCE / "giddy/LICENSE").read_bytes():
        raise SystemExit("Kanyaraasi's Giddy license notice differs from the pinned original")
    print("Six Kanyaraasi runtime SHA-256 checksums and the upstream MIT notice match")


if __name__ == "__main__":
    main()
