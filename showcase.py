"""Assemble a showcase video from a folder holding the script (one .md) and your Flow clips.

    python showcase.py showcase/glass-bottle

Scenes whose clip file (scene1.mp4 ...) is missing fall back to AI pictures, so you can preview the
timing before the clips exist. The result is build/<script name>/<script name>.mp4.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pipeline.util import BUILD_DIR, ROOT, slugify


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    folder = Path(sys.argv[1]).resolve()
    scripts = [p for p in folder.glob("*.md") if p.name.upper() != "FLOW_PROMPTS.MD"]
    if len(scripts) != 1:
        print(f"expected exactly one script .md in {folder}, found {len(scripts)}"); return 1
    script = scripts[0]
    missing = [f"scene{i}.mp4" for i in range(1, 6) if not (folder / f"scene{i}.mp4").exists()]
    print("missing clips (pictures used instead): " + (", ".join(missing) or "none"))
    rc = subprocess.run([sys.executable, "make_video.py", str(script), "--short"],
                        cwd=str(ROOT)).returncode
    slug = slugify(script.stem)
    print(f"output: {BUILD_DIR / slug / (slug + '.mp4')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())