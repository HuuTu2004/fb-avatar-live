from __future__ import annotations

from statistics import median
from typing import Sequence, TypeVar


T = TypeVar("T")


def scene_frame_map(scene_order: Sequence[int], frames_per_scene: int, source_scenes: int) -> list[int]:
    """Map a long scene timeline onto frames prepared from each unique scene once."""
    if frames_per_scene <= 0 or source_scenes <= 0:
        raise ValueError("frames_per_scene and source_scenes must be positive")
    if not scene_order or any(scene < 0 or scene >= source_scenes for scene in scene_order):
        raise ValueError("scene_order must contain valid source scene indexes")
    return [scene * frames_per_scene + frame for scene in scene_order for frame in range(frames_per_scene)]


def rotate_cycle(values: Sequence[T], offset: int) -> list[T]:
    if not values:
        return []
    start = offset % len(values)
    return list(values[start:]) + list(values[:start])


def smooth_bboxes(coords: Sequence[Sequence[float]], window: int) -> list[tuple[int, int, int, int]]:
    if window <= 1:
        return [tuple(map(int, coord)) for coord in coords]
    radius = window // 2
    filtered = []
    for index, coord in enumerate(coords):
        if not any(coord):
            filtered.append((0, 0, 0, 0))
            continue
        nearby = [item for item in coords[max(0, index - radius):index + radius + 1] if any(item)]
        filtered.append(tuple(median(item[axis] for item in nearby) for axis in range(4)))
    result = []
    for index, coord in enumerate(filtered):
        if not any(coords[index]):
            result.append((0, 0, 0, 0))
            continue
        nearby = [item for item in filtered[max(0, index - radius):index + radius + 1] if any(item)]
        result.append(tuple(round(sum(item[axis] for item in nearby) / len(nearby)) for axis in range(4)))
    return result
