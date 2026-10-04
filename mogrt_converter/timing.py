"""Changing a template's duration (Premiere's "Responsive Design – Time").

Protected regions (comp markers with "Protected Region" enabled, usually intro and outro)
always play at their original speed. The remaining, unprotected parts are stretched or
squeezed so the graphic fills the requested duration. Without protected regions the
whole animation is time-stretched.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any


def protected_regions(comp: Any) -> list[tuple[float, float]]:
    """Protected regions of a comp as merged, clamped (start, end) pairs in seconds."""
    mp = getattr(comp, "marker_property", None)
    if mp is None:
        return []
    fps = float(comp.frame_rate) or 25.0
    regs = []
    for k in mp.keyframes:
        v = k.value
        if not getattr(v, "protected_region", False):
            continue
        # py_aep's MarkerValue.duration is mis-scaled; frame_duration is in 1/1024 frames
        dur = float(getattr(v, "frame_duration", 0) or 0) / 1024.0 / fps
        s = max(0.0, float(k.time))
        e = min(float(comp.duration), s + dur)
        if e > s:
            regs.append((s, e))
    regs.sort()
    merged: list[tuple[float, float]] = []
    for s, e in regs:
        if merged and s <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


@dataclass
class TimeMap:
    """Maps output time (new duration) to comp time (original duration)."""

    src_duration: float
    out_duration: float
    regions: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        d, d2 = self.src_duration, self.out_duration
        protected = sum(e - s for s, e in self.regions)
        unprotected = d - protected
        # breakpoints: (out_time, comp_time); linear in between
        if not self.regions or unprotected <= 1e-6 or d2 <= protected + 1e-6:
            self.points = [(0.0, 0.0), (d2, d)]
            self.mode = "stretch"
            return
        k = (d2 - protected) / unprotected
        pts = [(0.0, 0.0)]
        t_out, t_src = 0.0, 0.0
        for s, e in self.regions:
            if s > t_src:  # unprotected gap before the region
                t_out += (s - t_src) * k
                t_src = s
                pts.append((t_out, t_src))
            t_out += e - s
            t_src = e
            pts.append((t_out, t_src))
        if t_src < d:
            t_out += (d - t_src) * k
            pts.append((t_out, d))
        self.points = pts
        self.mode = "protected"

    @property
    def is_identity(self) -> bool:
        return abs(self.out_duration - self.src_duration) < 1e-6

    def inverse(self, t_src: float) -> float:
        """Output time at which comp time t_src is shown (first occurrence)."""
        if self.is_identity:
            return t_src
        pts = self.points
        for (o0, s0), (o1, s1) in zip(pts, pts[1:]):
            if s0 <= t_src <= s1:
                return o0 if s1 - s0 < 1e-9 else o0 + (t_src - s0) * (o1 - o0) / (s1 - s0)
        return pts[-1][0] + (t_src - pts[-1][1])

    def __call__(self, t: float) -> float:
        if self.is_identity:
            return t
        pts = self.points
        outs = [p[0] for p in pts]
        i = bisect_right(outs, t) - 1
        if i < 0:
            return pts[0][1]
        if i >= len(pts) - 1:
            return pts[-1][1] + (t - pts[-1][0])
        (o0, s0), (o1, s1) = pts[i], pts[i + 1]
        if o1 - o0 < 1e-9:
            return s1
        return s0 + (t - o0) * (s1 - s0) / (o1 - o0)


def parse_duration(text: str | float | int, fps: float) -> float:
    """Seconds from '8', '8.5', '8,5', '8s', '00:00:08:12' (timecode) or '1:30'."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower().rstrip("s").strip()
    if not s:
        raise ValueError("leere Dauer")
    if ":" in s:
        parts = [float(p.replace(",", ".")) for p in s.split(":")]
        if len(parts) == 4:  # hh:mm:ss:ff
            h, m, sec, fr = parts
            return h * 3600 + m * 60 + sec + fr / fps
        if len(parts) == 3:  # hh:mm:ss
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:  # mm:ss
            return parts[0] * 60 + parts[1]
    if re.fullmatch(r"\d+f", s):  # frames, e.g. "200f"
        return int(s[:-1]) / fps
    return float(s.replace(",", "."))
