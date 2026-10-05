"""A small, freely redistributable test MOGRT, synthesized with py-aep.

Lower third: an orange bar slides in, a text sits on top, everything fades out at the end.
No Essential Graphics controls (py-aep cannot write those yet).

    python tests/sample_mogrt.py out.mogrt
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path


def make(out: str | Path, duration: float = 4.0, fps: float = 25.0) -> Path:
    import py_aep

    out = Path(out)
    app = py_aep.new()
    comp = app.project.root_folder.add_comp("Sample", 1920, 1080, 1.0, duration, fps)

    bar = comp.add_shape()
    contents = bar.property("ADBE Root Vectors Group").add_property("ADBE Vector Group").property("ADBE Vectors Group")
    rect = contents.add_property("ADBE Vector Shape - Rect")
    rect.property("ADBE Vector Rect Size").value = [900, 140]
    rect.property("ADBE Vector Rect Roundness").value = 12
    contents.add_property("ADBE Vector Graphic - Fill").property("ADBE Vector Fill Color").value = [0.9, 0.35, 0.05, 1]
    pos = bar.transform.property("ADBE Position")
    pos.set_value_at_time(0.0, [-500, 860, 0])
    pos.set_value_at_time(0.8, [700, 860, 0])
    opacity = bar.transform.property("ADBE Opacity")
    opacity.set_value_at_time(duration - 1.0, 100)
    opacity.set_value_at_time(duration, 0)

    text = comp.add_text("Jane Doe")
    text.transform.property("ADBE Position").value = [320, 885, 0]

    with tempfile.TemporaryDirectory() as tmp:
        aep = Path(tmp) / "Sample.aep"
        app.project.save(str(aep))
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.write(aep, "Sample.aep")
    definition = {"authorApp": "aefx", "capsuleName": "Sample", "clientControls": []}
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("definition.json", json.dumps(definition))
        z.writestr("project.aegraphic", inner.getvalue())
    return out


if __name__ == "__main__":
    print(make(sys.argv[1] if len(sys.argv) > 1 else "sample.mogrt"))
