"""Runs a Blender build script and exports the result: started by `godot-agent asset blender`.

    blender -b --factory-startup --python-exit-code 1 -P runner.py -- \
        --script build.py --out model.glb --seed 3 --params '{"leaf": "#c0602a"}' [--project dir]

Without --project, the project is the nearest folder above --out with a project.godot.
The kit's helper modules (gab, chibi) are importable by name.
"""

import json
import runpy
import sys
from pathlib import Path

argv = sys.argv[sys.argv.index("--") + 1:]
args = dict(zip(argv[::2], argv[1::2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gab  # noqa: E402

project = args.get("--project")
if not project:
    project = next((str(d) for d in Path(args["--out"]).resolve().parents if (d / "project.godot").exists()), None)
gab.setup(int(args.get("--seed", 1)), json.loads(args.get("--params", "{}")), project)
gab.clear()
runpy.run_path(args["--script"], run_name="__main__")
gab.export(args["--out"])
print(f"godot-agent: exported {args['--out']} ({gab.triangles()} triangles)")
