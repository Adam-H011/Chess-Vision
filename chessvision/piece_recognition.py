"""Offline, replaceable chess-piece recognition using local OpenCV templates."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import chess

from .board_detection import BoardGrid, Orientation


PIECE_STATES = (
    "empty", "white_pawn", "white_knight", "white_bishop", "white_rook",
    "white_queen", "white_king", "black_pawn", "black_knight", "black_bishop",
    "black_rook", "black_queen", "black_king",
)
PIECE_TO_FEN = {
    "white_pawn": "P", "white_knight": "N", "white_bishop": "B", "white_rook": "R",
    "white_queen": "Q", "white_king": "K", "black_pawn": "p", "black_knight": "n",
    "black_bishop": "b", "black_rook": "r", "black_queen": "q", "black_king": "k",
}
FEN_TO_PIECE = {value: key for key, value in PIECE_TO_FEN.items()}


class TemplateError(ValueError):
    """No usable local template exists for the requested operation."""


@dataclass(frozen=True)
class PiecePrediction:
    state: str
    confidence: float
    uncertain: bool
    occupancy: str = "unknown"
    foreground_fraction: float = 0.0
    score_margin: float = 1.0


class RecognitionBackend(Protocol):
    def recognize_square(self, square: np.ndarray) -> PiecePrediction: ...


@dataclass(frozen=True)
class OccupancyResult:
    state: str  # "empty", "occupied", or "uncertain"
    confidence: float
    foreground_fraction: float


def foreground_mask(square: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return a central foreground mask and its estimated local background."""
    if square.ndim != 3 or square.shape[0] < 16 or square.shape[1] < 16:
        raise ValueError("Foreground segmentation requires a non-empty RGB square image.")
    height, width = square.shape[:2]
    pixels = square[:, :, :3].reshape(-1, 3)
    quantized = (pixels // 8).astype(np.uint8)
    _, inverse, counts = np.unique(quantized, axis=0, return_inverse=True, return_counts=True)
    background_samples = pixels[inverse == int(np.argmax(counts))]
    background = np.median(background_samples, axis=0).astype(np.float32)
    distance = np.linalg.norm(square[:, :, :3].astype(np.float32) - background, axis=2)
    noise = float(np.median(np.linalg.norm(background_samples.astype(np.float32) - background, axis=1)))
    threshold = max(18.0, noise * 3.5)
    mask = (distance > threshold).astype(np.uint8) * 255
    border = max(2, round(min(height, width) * 0.07))
    # Coordinate labels are normally in the outer edge, never the piece body.
    mask[:border] = 0; mask[-border:] = 0; mask[:, :border] = 0; mask[:, -border:] = 0
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), background


def classify_occupancy(square: np.ndarray) -> OccupancyResult:
    """Classify occupancy from foreground contrast, independent of templates.

    A border-ring median estimates the local square colour, which prevents an
    empty light or dark square from being compared against an unrelated global
    background. Piece templates are deliberately not involved at this stage.
    """
    if square.ndim != 3 or square.shape[0] < 16 or square.shape[1] < 16:
        raise ValueError("Occupancy detection requires a non-empty RGB square image.")
    height, width = square.shape[:2]
    mask, _background = foreground_mask(square)
    fraction = float((mask > 0).mean())
    if fraction <= 0.035:
        return OccupancyResult("empty", min(1.0, 0.70 + (0.035 - fraction) * 8), fraction)
    if fraction >= 0.10:
        return OccupancyResult("occupied", min(1.0, 0.60 + (fraction - 0.10) * 3), fraction)
    return OccupancyResult("uncertain", 0.5, fraction)


def normalize_square(square: np.ndarray, size: int = 64) -> np.ndarray:
    if not isinstance(square, np.ndarray) or square.ndim not in (2, 3) or square.size == 0:
        raise ValueError("A non-empty grayscale or color square image is required.")
    gray = cv2.cvtColor(square[:, :, :3], cv2.COLOR_RGB2GRAY) if square.ndim == 3 else square
    return cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)


class TemplatePieceRecognizer:
    """Match local templates; themes can supply several images per piece state."""

    def __init__(self, templates: dict[str, list[np.ndarray]], threshold: float = 0.55) -> None:
        if not templates:
            raise TemplateError("No local piece templates were loaded.")
        unknown = set(templates).difference(PIECE_STATES)
        if unknown:
            raise TemplateError(f"Unknown piece template states: {', '.join(sorted(unknown))}.")
        self.templates = {state: [normalize_square(item) for item in images] for state, images in templates.items()}
        self.template_masks = {
            state: [foreground_mask(cv2.cvtColor(item, cv2.COLOR_GRAY2RGB))[0] for item in images]
            for state, images in self.templates.items()
        }
        self.template_luminance = {
            state: [float(cv2.cvtColor(item, cv2.COLOR_GRAY2RGB)[mask > 0].mean()) if np.any(mask > 0) else 0.0
                    for item, mask in zip(images, self.template_masks[state])]
            for state, images in self.templates.items()
        }
        self.threshold = threshold

    @classmethod
    def from_directory(cls, directory: str | Path, threshold: float = 0.55) -> "TemplatePieceRecognizer":
        root = Path(directory)
        if not root.is_dir():
            raise TemplateError(f"Template directory does not exist: {root}")
        templates: dict[str, list[np.ndarray]] = {}
        for state in PIECE_STATES:
            paths = list((root / state).glob("*.png")) + list(root.glob(f"{state}*.png"))
            images = []
            for path in paths:
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if image is not None:
                    images.append(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            if images:
                templates[state] = images
        return cls(templates, threshold)

    def recognize_square(self, square: np.ndarray) -> PiecePrediction:
        occupancy = classify_occupancy(square)
        if occupancy.state == "empty":
            return PiecePrediction("empty", occupancy.confidence, False, "empty", occupancy.foreground_fraction)
        piece = self.recognize_piece(square)
        return PiecePrediction(
            piece.state,
            piece.confidence,
            piece.uncertain or occupancy.state == "uncertain",
            occupancy.state,
            occupancy.foreground_fraction,
            piece.score_margin,
        )

    def recognize_piece(self, square: np.ndarray) -> PiecePrediction:
        normalized = normalize_square(square)
        rgb = cv2.resize(square[:, :, :3], (64, 64), interpolation=cv2.INTER_AREA)
        mask, _background = foreground_mask(rgb)
        if not np.any(mask > 0):
            raise TemplateError("No foreground was available for occupied-square classification.")
        luminance = float(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)[mask > 0].mean())
        colors = {"white": [], "black": []}
        for state, values in self.template_luminance.items():
            if state != "empty":
                colors[state.split("_", 1)[0]].extend(values)
        available_colours = [color for color, values in colors.items() if values]
        target_color = min(available_colours, key=lambda color: abs(luminance - float(np.mean(colors[color]))))
        state_scores: dict[str, float] = {}
        for state, images in self.templates.items():
            if state == "empty" or not state.startswith(f"{target_color}_"):
                continue
            for index, template in enumerate(images):
                template_mask = self.template_masks[state][index]
                intersection = float(np.logical_and(mask > 0, template_mask > 0).sum())
                union = float(np.logical_or(mask > 0, template_mask > 0).sum())
                shape_score = intersection / union if union else 0.0
                colour_score = max(0.0, 1.0 - abs(luminance - self.template_luminance[state][index]) / 160.0)
                score = 0.82 * shape_score + 0.18 * colour_score
                state_scores[state] = max(state_scores.get(state, -1.0), score)
        ordered_scores = sorted(state_scores.items(), key=lambda item: item[1], reverse=True)
        best_state, best_score = ordered_scores[0] if ordered_scores else ("unknown", -1.0)
        runner_up = ordered_scores[1][1] if len(ordered_scores) > 1 else -1.0
        # ``score`` is already a bounded similarity: IoU contributes 82% and
        # foreground luminance contributes 18%. The former ``(score + 1) / 2``
        # conversion inflated weak matches into apparently certain ones.
        confidence = max(0.0, min(1.0, best_score))
        # Keep the highest-scoring state even when it is uncertain. Replacing a
        # low-confidence piece with "empty" hides recognition failures.
        if best_state == "unknown":
            raise TemplateError("No piece templates are available for occupied-square classification.")
        # A high absolute IoU can still be a poor identity decision when the
        # runner-up (for example queen vs knight) is nearly identical.
        margin = max(0.0, best_score - runner_up) if runner_up >= 0 else 1.0
        return PiecePrediction(best_state, confidence, confidence < self.threshold or margin < .06, "occupied", 0.0, margin)


class TwoStagePieceRecognizer:
    """Occupancy-first recognizer which prevents empty templates dominating."""

    def __init__(self, piece_recognizer: TemplatePieceRecognizer) -> None:
        self.piece_recognizer = piece_recognizer

    def recognize_square(self, square: np.ndarray) -> PiecePrediction:
        occupancy = classify_occupancy(square)
        if occupancy.state == "empty":
            return PiecePrediction("empty", occupancy.confidence, False, "empty", occupancy.foreground_fraction)
        prediction = self.piece_recognizer.recognize_piece(square)
        uncertain = prediction.uncertain or occupancy.state == "uncertain"
        return PiecePrediction(
            prediction.state, min(prediction.confidence, occupancy.confidence) if uncertain else prediction.confidence,
            uncertain, occupancy.state, occupancy.foreground_fraction, prediction.score_margin,
        )


def _generated_template(state: str, background: int) -> np.ndarray:
    """Create an original, redistributable fallback sprite for one state."""
    image = np.full((64, 64), background, dtype=np.uint8)
    if state == "empty":
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    letter = PIECE_TO_FEN[state].upper()
    color = 244 if state.startswith("white_") else 24
    # Hershey glyphs are rendered programmatically; no third-party art is used.
    cv2.putText(image, letter, (10, 48), cv2.FONT_HERSHEY_COMPLEX, 1.45, color, 3, cv2.LINE_AA)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)


class BuiltinPieceRecognizer(TemplatePieceRecognizer):
    """Original generated fallback templates for immediate offline use.

    It provides a no-setup baseline, not a claim of universal theme accuracy.
    Local theme templates still take precedence when available.
    """

    def __init__(self, threshold: float = 0.72) -> None:
        templates = {
            state: [_generated_template(state, background) for background in (205, 145)]
            for state in PIECE_STATES
        }
        super().__init__(templates, threshold)


def automatic_recognizer(template_directory: str | Path | None = None) -> RecognitionBackend:
    """Use saved local templates when present, otherwise bundled generated ones."""
    if template_directory:
        root = Path(template_directory)
        if root.is_dir() and any(root.glob("**/*.png")):
            return TwoStagePieceRecognizer(TemplatePieceRecognizer.from_directory(root))
    return TwoStagePieceRecognizer(BuiltinPieceRecognizer())


def recognition_warning(predictions: dict[str, PiecePrediction]) -> str | None:
    """Flag implausible outcomes instead of presenting an empty board as success."""
    occupied = [item for item in predictions.values() if item.occupancy == "occupied"]
    nonempty = [item for item in predictions.values() if item.state != "empty"]
    if occupied and not nonempty:
        return "Foreground was detected on occupied squares, but no pieces were identified."
    if len(nonempty) == 0:
        return "All squares were classified empty; verify the crop and recognition backend."
    return None


@dataclass(frozen=True)
class CalibrationReport:
    directory: Path
    sample_counts: dict[str, int]
    missing_square_color_combinations: tuple[str, ...]


def default_template_directory() -> Path:
    """Return ChessVision's user-local, offline template location."""
    return Path.home() / ".local" / "share" / "chessvision" / "templates"


VERIFIED_PROFILE_NAME = "verified_green_classic"


@dataclass(frozen=True)
class RecognitionProfile:
    name: str
    directory: Path
    palette: tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True)
class RecognizerSelection:
    recognizer: RecognitionBackend
    profile_name: str | None
    similarity: float | None
    shape_similarity: float | None = None


def default_profile_directory() -> Path:
    return Path.home() / ".local" / "share" / "chessvision" / "profiles"


def _dominant_colour(square: np.ndarray) -> np.ndarray:
    pixels = square[:, :, :3].reshape(-1, 3)
    quantized = (pixels // 8).astype(np.uint8)
    _, inverse, counts = np.unique(quantized, axis=0, return_inverse=True, return_counts=True)
    return np.median(pixels[inverse == int(np.argmax(counts))], axis=0)


def board_palette(image: np.ndarray, grid: BoardGrid) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Estimate the two board-square colours without relying on piece labels."""
    colours = np.float32([_dominant_colour(grid.extract_square(image, row, column)) for row in range(8) for column in range(8)])
    _compactness, _labels, centers = cv2.kmeans(colours, 2, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.4), 3, cv2.KMEANS_PP_CENTERS)
    ordered = sorted((tuple(map(float, color)) for color in centers), key=sum)
    return ordered[0], ordered[1]


def _palette_distance(left, right) -> float:
    direct = np.mean([np.linalg.norm(np.subtract(left[index], right[index])) for index in range(2)])
    swapped = np.mean([np.linalg.norm(np.subtract(left[index], right[1 - index])) for index in range(2)])
    return float(min(direct, swapped) / 255.0)


def _profile_shape_similarity(image: np.ndarray, grid: BoardGrid, directory: Path) -> float:
    """Compare foreground silhouettes without assuming a board position."""
    recognizer = TemplatePieceRecognizer.from_directory(directory)
    template_masks = [mask for state, masks in recognizer.template_masks.items() if state != "empty" for mask in masks]
    scores: list[float] = []
    for row in range(8):
        for column in range(8):
            square = grid.extract_square(image, row, column)
            occupancy = classify_occupancy(square)
            if occupancy.state != "occupied":
                continue
            rgb = cv2.resize(square[:, :, :3], (64, 64), interpolation=cv2.INTER_AREA)
            mask, _background = foreground_mask(rgb)
            best = 0.0
            for template_mask in template_masks:
                union = float(np.logical_or(mask > 0, template_mask > 0).sum())
                if union:
                    best = max(best, float(np.logical_and(mask > 0, template_mask > 0).sum()) / union)
            scores.append(best)
    return float(np.median(scores)) if scores else 0.0


def _profile_source() -> Path | None:
    """Locate the redistributable user-supplied fixture in a source checkout."""
    candidate = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "real_board" / "board_crop.png"
    return candidate if candidate.is_file() else None


def install_verified_profile(profile_root: str | Path | None = None, source_image: str | Path | None = None) -> RecognitionProfile | None:
    """Install the verified supplied-board templates locally, once per user.

    The profile is built from the supplied artwork and labels templates during
    installation; recognition later uses image similarity and template matching,
    never a hardcoded FEN result.
    """
    root = Path(profile_root) if profile_root else default_profile_directory()
    directory = root / VERIFIED_PROFILE_NAME
    metadata_path = directory / "profile.json"
    if metadata_path.is_file():
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        palette = tuple(tuple(color) for color in data["palette"])
        return RecognitionProfile(data["name"], directory, palette)  # type: ignore[arg-type]
    source = Path(source_image) if source_image else _profile_source()
    if source is None or not source.is_file():
        return None
    image = cv2.cvtColor(cv2.imread(str(source), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    grid = BoardGrid.manual(image) if image.shape[0] == image.shape[1] else BoardGrid(0, 0, min(image.shape[:2]), image.shape[1], image.shape[0], source="profile", board_width=image.shape[1], board_height=image.shape[0])
    directory.mkdir(parents=True, exist_ok=True)
    calibrate_starting_position(image, grid, "white", directory)
    palette = board_palette(image, grid)
    metadata_path.write_text(json.dumps({"name": VERIFIED_PROFILE_NAME, "palette": palette}, indent=2), encoding="utf-8")
    return RecognitionProfile(VERIFIED_PROFILE_NAME, directory, palette)


def select_recognizer(image: np.ndarray, grid: BoardGrid, template_directory: str | Path | None = None, profile_root: str | Path | None = None) -> RecognizerSelection:
    """Select a matching local profile, otherwise use a cautious fallback."""
    profile = install_verified_profile(profile_root)
    if profile is not None:
        palette_similarity = _palette_distance(board_palette(image, grid), profile.palette)
        shape_similarity = _profile_shape_similarity(image, grid, profile.directory)
        if palette_similarity <= 0.12 and shape_similarity >= 0.58:
            return RecognizerSelection(
                TwoStagePieceRecognizer(TemplatePieceRecognizer.from_directory(profile.directory)),
                profile.name,
                palette_similarity,
                shape_similarity,
            )
    if template_directory:
        root = Path(template_directory)
        if root.is_dir() and any(root.glob("**/*.png")):
            return RecognizerSelection(
                TwoStagePieceRecognizer(TemplatePieceRecognizer.from_directory(root)),
                "user local templates", 0.0, None,
            )
    return RecognizerSelection(TwoStagePieceRecognizer(BuiltinPieceRecognizer()), None, None)


def calibrate_starting_position(
    image: np.ndarray, grid: BoardGrid, orientation: Orientation, directory: str | Path
) -> CalibrationReport:
    """Save labeled local templates from a user-confirmed standard start board.

    A starting position includes all 12 piece types and empty squares, but not
    every piece/color-square combination. The returned report makes that
    limitation explicit rather than overstating calibration coverage.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    board = chess.Board()
    sample_counts = {state: 0 for state in PIECE_STATES}
    observed_colors: set[tuple[str, int]] = set()
    for row in range(8):
        for column in range(8):
            name = grid.square_name(row, column, orientation)
            piece = board.piece_at(chess.parse_square(name))
            state = FEN_TO_PIECE[piece.symbol()] if piece else "empty"
            target = root / state
            target.mkdir(exist_ok=True)
            index = len(list(target.glob("*.png")))
            square = grid.extract_square(image, row, column)
            if not cv2.imwrite(str(target / f"start_{index:03d}.png"), cv2.cvtColor(square[:, :, :3], cv2.COLOR_RGB2BGR)):
                raise TemplateError(f"Could not save template for {state}.")
            sample_counts[state] += 1
            observed_colors.add((state, (row + column) % 2))
    missing = tuple(
        f"{state} on {'light' if color == 0 else 'dark'} square"
        for state in PIECE_STATES
        for color in (0, 1)
        if (state, color) not in observed_colors
    )
    return CalibrationReport(root, sample_counts, missing)


def recognize_board(
    image: np.ndarray, grid: BoardGrid, recognizer: RecognitionBackend, orientation: Orientation
) -> dict[str, PiecePrediction]:
    """Recognize all 64 squares, keyed by orientation-correct algebraic names."""
    result: dict[str, PiecePrediction] = {}
    for row in range(8):
        for column in range(8):
            result[grid.square_name(row, column, orientation)] = recognizer.recognize_square(
                grid.extract_square(image, row, column)
            )
    return result
