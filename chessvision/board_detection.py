"""Detect and describe an 8×8 chessboard grid in a local RGB image."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np


class BoardDetectionError(ValueError):
    """The selected image cannot be interpreted as a chessboard region."""


Orientation = Literal["white", "black"]


@dataclass(frozen=True)
class BoardGrid:
    """An axis-aligned board and its 64 equally sized image squares."""

    x: int
    y: int
    size: int
    image_width: int
    image_height: int
    source: str = "automatic"
    board_width: int | None = None
    board_height: int | None = None

    @property
    def square_size(self) -> float:
        return self.size / 8

    @property
    def square_width(self) -> float:
        return (self.board_width or self.size) / 8

    @property
    def square_height(self) -> float:
        return (self.board_height or self.size) / 8

    def square_bounds(self, row: int, column: int) -> tuple[int, int, int, int]:
        if not 0 <= row < 8 or not 0 <= column < 8:
            raise BoardDetectionError("Board row and column must be in the range 0–7.")
        left = round(self.x + column * self.square_width)
        top = round(self.y + row * self.square_height)
        right = round(self.x + (column + 1) * self.square_width)
        bottom = round(self.y + (row + 1) * self.square_height)
        return left, top, right, bottom

    def square_name(self, row: int, column: int, orientation: Orientation) -> str:
        if orientation not in ("white", "black"):
            raise BoardDetectionError("Orientation must be 'white' or 'black'.")
        file_index = column if orientation == "white" else 7 - column
        rank = 8 - row if orientation == "white" else row + 1
        return f"{'abcdefgh'[file_index]}{rank}"

    def extract_square(self, image: np.ndarray, row: int, column: int) -> np.ndarray:
        _validate_image(image)
        left, top, right, bottom = self.square_bounds(row, column)
        if right > image.shape[1] or bottom > image.shape[0]:
            raise BoardDetectionError("Grid extends beyond the source image.")
        return image[top:bottom, left:right].copy()

    @classmethod
    def manual(cls, image: np.ndarray, bounds: tuple[int, int, int, int] | None = None) -> "BoardGrid":
        """Create the reliable manual fallback from square board bounds.

        ``bounds`` is ``(x, y, width, height)``. If omitted, the largest
        centered square in the selected image is used.
        """
        _validate_image(image)
        height, width = image.shape[:2]
        if bounds is None:
            size = min(width, height)
            x, y = (width - size) // 2, (height - size) // 2
        else:
            x, y, board_width, board_height = bounds
            if board_width <= 0 or board_height <= 0:
                raise BoardDetectionError("Manual board bounds must have positive dimensions.")
            size = min(board_width, board_height)
        if size < 8 or x < 0 or y < 0 or x + size > width or y + size > height:
            raise BoardDetectionError("Manual board bounds are outside the selected image.")
        return cls(x, y, size, width, height, source="manual", board_width=board_width if bounds else size, board_height=board_height if bounds else size)


def _validate_image(image: np.ndarray) -> None:
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] not in (3, 4):
        raise BoardDetectionError("Expected an RGB or RGBA NumPy image.")
    if image.shape[0] < 16 or image.shape[1] < 16:
        raise BoardDetectionError("Image is too small to contain an 8×8 board.")


def detect_board_grid(image: np.ndarray) -> BoardGrid:
    """Find the largest near-square board-shaped contour.

    The detector intentionally has a manual fallback because themes without a
    visible border cannot be identified from geometry alone.
    """
    _validate_image(image)
    height, width = image.shape[:2]
    # A user-selected crop that is nearly square is the most reliable grid:
    # display scaling can make its horizontal and vertical pixel pitches differ.
    if abs(width - height) > 3 and 0.94 <= width / height <= 1.06:
        return BoardGrid(0, 0, min(width, height), width, height, source="crop", board_width=width, board_height=height)
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 40, 130)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, int, int, int]] = []
    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        size = min(candidate_width, candidate_height)
        if size < min(width, height) * 0.45:
            continue
        ratio = candidate_width / candidate_height if candidate_height else 0
        if not 0.88 <= ratio <= 1.12:
            continue
        area = cv2.contourArea(contour)
        rectangularity = area / max(candidate_width * candidate_height, 1)
        candidates.append((size * (0.5 + rectangularity), x, y, size))
    if not candidates:
        raise BoardDetectionError(
            "Could not detect a square board boundary. Use manual board bounds instead."
        )
    _, x, y, size = max(candidates)
    x = max(0, min(x, width - size))
    y = max(0, min(y, height - size))
    return BoardGrid(x, y, size, width, height, board_width=size, board_height=size)


def detect_or_manual(image: np.ndarray, bounds: tuple[int, int, int, int] | None = None) -> BoardGrid:
    """Detect a grid, or explicitly use caller-provided manual bounds."""
    return BoardGrid.manual(image, bounds) if bounds is not None else detect_board_grid(image)
