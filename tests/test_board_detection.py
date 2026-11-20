from __future__ import annotations

import cv2
import numpy as np
import pytest

from chessvision.board_detection import BoardDetectionError, BoardGrid, detect_board_grid


def synthetic_board(size: int = 320, margin: int = 12) -> np.ndarray:
    image = np.full((size + 2 * margin, size + 2 * margin, 3), 230, dtype=np.uint8)
    square = size // 8
    for row in range(8):
        for column in range(8):
            color = (240, 217, 181) if (row + column) % 2 == 0 else (181, 136, 99)
            image[margin + row * square:margin + (row + 1) * square,
                  margin + column * square:margin + (column + 1) * square] = color
    cv2.rectangle(image, (margin, margin), (margin + size - 1, margin + size - 1), (20, 20, 20), 3)
    return image


def test_detect_grid_with_margin_and_square_coordinates() -> None:
    image = synthetic_board()
    grid = detect_board_grid(image)
    assert abs(grid.x - 12) <= 3 and abs(grid.y - 12) <= 3
    assert abs(grid.size - 320) <= 6
    assert grid.square_bounds(0, 0)[2] - grid.square_bounds(0, 0)[0] in range(39, 42)


def test_orientation_mapping_is_not_mirrored() -> None:
    grid = BoardGrid.manual(synthetic_board())
    assert grid.square_name(0, 0, "white") == "a8"
    assert grid.square_name(7, 7, "white") == "h1"
    assert grid.square_name(0, 0, "black") == "h1"
    assert grid.square_name(7, 7, "black") == "a8"


def test_manual_grid_and_square_extraction() -> None:
    image = synthetic_board(160, 8)
    grid = BoardGrid.manual(image, (8, 8, 160, 160))
    assert grid.extract_square(image, 3, 4).shape[:2] == (20, 20)


@pytest.mark.parametrize("image", [np.zeros((8, 8, 3), dtype=np.uint8), np.zeros((80, 80), dtype=np.uint8)])
def test_invalid_images_fail_clearly(image: np.ndarray) -> None:
    with pytest.raises(BoardDetectionError):
        detect_board_grid(image)
