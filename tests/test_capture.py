from __future__ import annotations

import numpy as np
import pytest

from chessvision.capture import (
    CaptureError,
    CaptureRegion,
    GnomePortalPipeWireBackend,
    InvalidRegionError,
    PortalCancelledError,
    PortalScreenCapture,
    crop_frame,
    frame_from_rgb_buffer,
)


class FakeBackend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.callback = None
        self.started = False
        self.stopped = False

    def start(self, callback) -> None:
        self.started = True
        self.callback = callback
        if self.error:
            raise self.error

    def stop(self) -> None:
        self.stopped = True


def test_capture_initialization_and_frame_storage() -> None:
    backend = FakeBackend()
    capture = PortalScreenCapture(backend)
    capture.start()
    backend.callback(np.full((2, 3, 3), 7, dtype=np.uint8))
    assert capture.is_running
    assert capture.latest_frame().shape == (2, 3, 3)
    frame, info = capture.latest_frame_with_info()
    assert frame.shape == (2, 3, 3)
    assert info.sequence_id == 1 and (info.width, info.height) == (3, 2)
    backend.callback(np.full((2, 3, 3), 8, dtype=np.uint8))
    assert capture.latest_frame_with_info()[1].sequence_id == 2


def test_permission_denial_stops_backend() -> None:
    backend = FakeBackend(PortalCancelledError("denied"))
    with pytest.raises(PortalCancelledError):
        PortalScreenCapture(backend).start()
    assert backend.started and backend.stopped


def test_portal_cancel_and_error_response_mapping() -> None:
    with pytest.raises(PortalCancelledError):
        GnomePortalPipeWireBackend._raise_for_response(1)
    with pytest.raises(CaptureError):
        GnomePortalPipeWireBackend._raise_for_response(2)


def test_portal_tokens_create_predictable_request_paths() -> None:
    backend = GnomePortalPipeWireBackend()
    backend._sender_path = backend._sender_component(":1.42")
    token = backend._new_token("start")
    assert token.startswith("chessvision_start_")
    assert backend._expected_request_path(token) == (
        "/org/freedesktop/portal/desktop/request/1_42/" + token
    )


def test_portal_options_do_not_double_wrap_variants() -> None:
    marker = object()
    options = GnomePortalPipeWireBackend._options(None, types=marker)
    assert options == {"types": marker}


def test_session_cleanup() -> None:
    backend = FakeBackend()
    capture = PortalScreenCapture(backend)
    capture.start()
    capture.stop()
    assert backend.stopped
    assert not capture.is_running
    assert capture.latest_frame() is None


def test_rgb_frame_conversion_honors_stride() -> None:
    data = bytes([1, 2, 3, 4, 5, 6, 99, 99, 7, 8, 9, 10, 11, 12, 88, 88])
    frame = frame_from_rgb_buffer(data, width=2, height=2, stride=8)
    assert frame.tolist() == [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]]


def test_region_crop_is_a_copy() -> None:
    frame = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
    cropped = crop_frame(frame, CaptureRegion(1, 1, 3, 2))
    assert cropped.shape == (2, 3, 3)
    cropped[0, 0, 0] = 0
    assert frame[1, 1, 0] != 0


@pytest.mark.parametrize(
    "region",
    [CaptureRegion(-1, 0, 1, 1), CaptureRegion(0, 0, 0, 1), CaptureRegion(3, 3, 2, 2)],
)
def test_invalid_region_selection(region: CaptureRegion) -> None:
    with pytest.raises(InvalidRegionError):
        crop_frame(np.zeros((4, 4, 3), dtype=np.uint8), region)
