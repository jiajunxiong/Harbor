"""Chart downsampling (MVP 5 / SP 5.15).

A run's net-value series spans >1600 days. Sending every point is fine for one
curve but wasteful once several runs are compared, so the API can subsample the
series it *draws*.

Two rules keep this honest:

1. **Display only.** Analytics — metrics and drawdown intervals — are always
   computed server-side on the full series before any subsampling, so a
   threshold interval on the chart can never be an artefact of the sampling.
2. **Say so.** The response reports ``point_count``, ``returned_count`` and
   ``downsampled``, and the UI states that the curve is a subsample, so nobody
   reads a smoothed line as the raw series.

The algorithm is Largest-Triangle-Three-Buckets (LTTB), the standard choice for
financial series: it preserves visual shape and, unlike naive stride sampling, it
keeps the local extremes that make spikes and troughs visible. The first and last
points are always retained so the curve still starts and ends on the real values.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]


def largest_triangle_three_buckets(
    points: Sequence[Point],
    threshold: int,
) -> list[Point]:
    """Subsample ``points`` down to ``threshold`` points, preserving shape.

    Args:
        points: ``(x, y)`` pairs in ascending ``x`` order.
        threshold: The target number of points. Values at or above
            ``len(points)`` return the input unchanged.

    Returns:
        The subsampled points. The first and last input points are always kept.

    Raises:
        ValueError: If ``threshold`` is not a positive integer or the input is
            not in ascending ``x`` order.
    """
    if threshold <= 0:
        raise ValueError("threshold must be a positive integer.")
    if len(points) <= threshold:
        return list(points)
    if any(points[index][0] > points[index + 1][0] for index in range(len(points) - 1)):
        raise ValueError("points must be in ascending x order.")
    if threshold == 1:
        return [points[-1]]
    if threshold == 2:
        return [points[0], points[-1]]

    sampled: list[Point] = [points[0]]
    every = (len(points) - 2) / (threshold - 2)
    anchor = 0

    for bucket in range(threshold - 2):
        # Average of the *next* bucket forms the triangle's third vertex.
        next_start = int(math.floor((bucket + 1) * every)) + 1
        next_end = min(int(math.floor((bucket + 2) * every)) + 1, len(points))
        if next_start >= next_end:
            next_start = min(next_start, len(points) - 1)
            next_end = next_start + 1
        span = next_end - next_start
        average_x = sum(points[index][0] for index in range(next_start, next_end)) / span
        average_y = sum(points[index][1] for index in range(next_start, next_end)) / span

        current_start = int(math.floor(bucket * every)) + 1
        current_end = min(int(math.floor((bucket + 1) * every)) + 1, len(points))
        anchor_x, anchor_y = points[anchor]

        largest_area = -1.0
        chosen = current_start
        for index in range(current_start, max(current_end, current_start + 1)):
            point_x, point_y = points[index]
            area = abs(
                (anchor_x - average_x) * (point_y - anchor_y)
                - (anchor_x - point_x) * (average_y - anchor_y)
            )
            if area > largest_area:
                largest_area = area
                chosen = index
        sampled.append(points[chosen])
        anchor = chosen

    sampled.append(points[-1])
    return sampled
