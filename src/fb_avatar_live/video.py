from __future__ import annotations

from statistics import median
from typing import Sequence, TypeVar


T = TypeVar("T")


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
