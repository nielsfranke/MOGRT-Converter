"""Smooth playback: render preview frames ahead on several processes and keep them in memory.

Rendering holds the GIL, so threads don't help. Worker processes each keep a Renderer per
template and receive the full preview state with every task (values, duration, motion, scale).
Finished frames are PNG bytes, cached per state key until the state changes.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sys
import threading
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any

MAX_BYTES = 600 * 1024 * 1024  # all cached states together

# ------------------------------------------------------------------ worker process
_renderers: dict[str, Any] = {}
_state: dict[str, str] = {}


def _init_worker() -> None:
    """Workers run at low priority so the window and the preview stay responsive (issue #4)."""
    try:
        if sys.platform == "win32":
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x4000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except Exception:
        pass


def _render(path: str, state: dict, frames: list[int], fps: float) -> list[tuple[int, bytes]]:
    import skia

    from ..mogrt import load
    from ..render.compositor import Renderer

    r = _renderers.get(path)
    if r is None:
        r = _renderers[path] = Renderer(load(path), {}, scale=1.0)
    key = state_key(path, state)
    if _state.get(path) != key:
        r.set_values(state.get("values") or {})
        dur = state.get("duration")
        r.set_duration(float(dur) if dur else None)
        r.set_motion(state.get("motion"))
        _state[path] = key
    r.scale = float(state["scale"])
    out = []
    for f in frames:
        img = r.render_frame(f / fps)
        out.append((f, bytes(img.encodeToData(skia.EncodedImageFormat.kPNG, 100))))
    return out


# ------------------------------------------------------------------ server side
def state_key(path: str, state: dict) -> str:
    keep = {k: state.get(k) for k in ("values", "duration", "motion", "scale")}
    keep["path"] = path
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()


def workers() -> int:
    # cpu_count() includes hyperthreads; every worker holds its own copy of the template in memory
    return max(2, min(6, (os.cpu_count() or 4) // 2))


class Prefetcher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pool: ProcessPoolExecutor | None = None
        self.frames: OrderedDict[str, dict[int, bytes]] = OrderedDict()  # state key -> frame -> png
        self.size = 0
        self.active: tuple[str, str] | None = None  # (path, state key) being prefetched
        self.futures: list[Future] = []

    def get(self, key: str, frame: int) -> bytes | None:
        with self.lock:
            fr = self.frames.get(key)
            return fr.get(frame) if fr else None

    def put(self, key: str, frame: int, data: bytes) -> None:
        with self.lock:
            fr = self.frames.setdefault(key, {})
            self.frames.move_to_end(key)
            if frame not in fr:
                fr[frame] = data
                self.size += len(data)
            while self.size > MAX_BYTES and len(self.frames) > 1:
                _, old = self.frames.popitem(last=False)
                self.size -= sum(map(len, old.values()))

    def cached(self, key: str) -> list[int]:
        with self.lock:
            return sorted(self.frames.get(key, {}))

    def start(self, path: str, state: dict, fps: float, count: int, first: int = 0) -> str:
        """Render all frames of this state in the background, starting at `first`."""
        key = state_key(path, state)
        with self.lock:
            if self.active == (path, key) and any(not f.done() for f in self.futures):
                return key
            for f in self.futures:
                f.cancel()
            self.futures = []
            self.active = (path, key)
            have = set(self.frames.get(key, {}))
            if self.pool is None:
                # always spawn: forking the threaded server process can deadlock the child (Linux default)
                self.pool = ProcessPoolExecutor(max_workers=workers(), mp_context=multiprocessing.get_context("spawn"),
                                                initializer=_init_worker)
            pool = self.pool
        todo = [f for f in list(range(first, count)) + list(range(0, first)) if f not in have]
        n = workers()
        # interleave so the frames right after the playhead arrive first
        chunks = [todo[i:i + n * 2] for i in range(0, len(todo), n * 2)]
        futures = []
        for chunk in chunks:
            for w in range(n):
                part = chunk[w::n]
                if not part:
                    continue
                fut = pool.submit(_render, path, state, part, fps)
                fut.add_done_callback(lambda f, k=key: self._done(k, f))
                futures.append(fut)
        with self.lock:
            if self.active == (path, key):
                self.futures = futures
            else:  # superseded meanwhile
                for f in futures:
                    f.cancel()
        return key

    def _done(self, key: str, fut: Future) -> None:
        if fut.cancelled():
            return
        try:
            for frame, data in fut.result():
                self.put(key, frame, data)
        except Exception as e:  # a broken frame must not kill playback
            print(f"Vorausrendern fehlgeschlagen: {e}")

    def stop(self) -> None:
        with self.lock:
            for f in self.futures:
                f.cancel()
            self.futures = []
            self.active = None

    def forget(self, path: str) -> None:
        """Drop worker renderers for a template (e.g. after new fonts were installed)."""
        self.stop()
        with self.lock:
            self.frames.clear()
            self.size = 0
            pool, self.pool = self.pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


PREFETCH = Prefetcher()
