#!/usr/bin/env python3
"""Build a self-contained QPegel plugin zip.

Bundles the pure-Python dependencies from requirements-bundle.txt into
QPegel/ext_libs/ so the plugin works without QPIP or internet access.

Usage:
    python scripts/build_zip.py [--output dist/QPegel.zip]

Only files tracked by git are packaged, so local files such as .env,
login.py or test data never end up in the zip.
"""

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "QPegel"

# git pathspecs; ":(glob)*.py" only matches files in the repo root
INCLUDE = [
    ":(glob)*.py",
    ":(glob)*.ui",
    "metadata.txt",
    "Logo.png",
    "LICENSE",
    "README.md",
    "img_ui",
    "layer-styles",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist" / f"{PLUGIN_NAME}.zip"
    )
    output = parser.parse_args().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", *INCLUDE],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(filter(None, tracked)):
            zf.write(ROOT / rel, f"{PLUGIN_NAME}/{rel}")

        with tempfile.TemporaryDirectory() as libs:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    libs,
                    "--no-deps",
                    "--only-binary=:all:",
                    "--no-compile",
                    "--disable-pip-version-check",
                    "-r",
                    str(ROOT / "requirements-bundle.txt"),
                ],
                check=True,
            )
            # bin/ (console scripts) is useless inside a QGIS plugin;
            # *.dist-info is kept: it carries the license files
            for file in sorted(Path(libs).rglob("*")):
                rel = file.relative_to(libs)
                if file.is_file() and rel.parts[0] != "bin":
                    zf.write(file, f"{PLUGIN_NAME}/ext_libs/{rel.as_posix()}")

    print(f"Built {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
