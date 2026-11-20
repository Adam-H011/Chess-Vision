"""GNOME Wayland screen capture through XDG Desktop Portal and PipeWire.

This module deliberately does not use X11 or direct desktop capture APIs. The
portal owns the permission prompt and chooses the monitor/window; PipeWire
delivers only the stream the user approved.
"""

from __future__ import annotations

import threading
import logging
import secrets
from dataclasses import dataclass
from typing import Callable, Protocol

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised on minimal installations
    np = None  # type: ignore[assignment]


class CaptureError(RuntimeError):
    """Base error for portal or PipeWire capture failures."""


class CaptureUnavailableError(CaptureError):
    """GNOME's GObject/GStreamer bindings are not installed."""


class PermissionDeniedError(CaptureError):
    """The user rejected or cancelled the portal sharing request."""


class PortalCancelledError(PermissionDeniedError):
    """The portal returned its cancellation response."""


class InvalidRegionError(ValueError):
    """A requested image region is empty or outside its source frame."""


@dataclass(frozen=True)
class CaptureRegion:
    """A rectangular selection in preview-image pixels."""

    x: int
    y: int
    width: int
    height: int


FrameCallback = Callable[["np.ndarray"], None]
LOGGER = logging.getLogger(__name__)


class CaptureBackend(Protocol):
    """Small backend contract that lets tests run without a GNOME desktop."""

    def start(self, on_frame: FrameCallback) -> None: ...

    def stop(self) -> None: ...


def _require_numpy() -> None:
    if np is None:
        raise CaptureUnavailableError("NumPy is required for screen capture frames.")


def frame_from_rgb_buffer(
    data: bytes | memoryview, width: int, height: int, stride: int
) -> "np.ndarray":
    """Copy a packed RGB PipeWire buffer into a contiguous NumPy image."""
    _require_numpy()
    if width <= 0 or height <= 0 or stride < width * 3:
        raise ValueError("Invalid RGB frame dimensions or stride.")
    required = stride * height
    if len(data) < required:
        raise ValueError("RGB buffer is shorter than its declared dimensions.")
    rows = np.frombuffer(data, dtype=np.uint8, count=required).reshape(height, stride)
    return rows[:, : width * 3].reshape(height, width, 3).copy()


def crop_frame(frame: "np.ndarray", region: CaptureRegion) -> "np.ndarray":
    """Return a copied RGB region, suitable for later OpenCV processing."""
    _require_numpy()
    if frame.ndim != 3 or frame.shape[2] not in (3, 4):
        raise InvalidRegionError("Frame must have RGB or RGBA channels.")
    if region.width <= 0 or region.height <= 0:
        raise InvalidRegionError("Region width and height must be positive.")
    if region.x < 0 or region.y < 0:
        raise InvalidRegionError("Region coordinates must not be negative.")
    if region.x + region.width > frame.shape[1] or region.y + region.height > frame.shape[0]:
        raise InvalidRegionError("Region extends outside the captured frame.")
    return frame[region.y : region.y + region.height, region.x : region.x + region.width].copy()


class PortalScreenCapture:
    """Thread-safe capture facade used by the GUI and future board detector."""

    def __init__(self, backend: CaptureBackend | None = None) -> None:
        self._backend = backend or GnomePortalPipeWireBackend()
        self._lock = threading.Lock()
        self._latest_frame: "np.ndarray | None" = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        try:
            self._backend.start(self._on_frame)
        except CaptureError:
            self._backend.stop()
            raise
        except Exception as exc:
            self._backend.stop()
            raise CaptureError(f"Unable to connect to the GNOME capture portal: {exc}") from exc
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        try:
            self._backend.stop()
        finally:
            self._running = False
            with self._lock:
                self._latest_frame = None

    def latest_frame(self) -> "np.ndarray | None":
        """Return a copy of the latest frame, or ``None`` before one arrives."""
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def selected_region(self, region: CaptureRegion) -> "np.ndarray":
        frame = self.latest_frame()
        if frame is None:
            raise CaptureError("No captured frame is available yet.")
        return crop_frame(frame, region)

    def _on_frame(self, frame: "np.ndarray") -> None:
        with self._lock:
            self._latest_frame = frame.copy()


class GnomePortalPipeWireBackend:
    """XDG ScreenCast portal request and GStreamer PipeWire consumer.

    This backend requires Fedora packages ``python3-gobject``, ``gstreamer1``
    and ``gstreamer1-plugins-base``. Portal calls occur in the caller's worker
    thread, keeping the Qt event loop responsive while GNOME shows its dialog.
    """

    PORTAL = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    SCREENCAST = "org.freedesktop.portal.ScreenCast"
    REQUEST = "org.freedesktop.portal.Request"

    def __init__(self) -> None:
        self._callback: FrameCallback | None = None
        self._pipeline = None
        self._bus = None
        self._session_handle: str | None = None
        self._context = None
        self._sender_path = ""

    @staticmethod
    def _libraries():
        try:
            import gi

            gi.require_version("Gio", "2.0")
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gio, GLib, Gst, GstVideo
        except (ImportError, ValueError) as exc:
            raise CaptureUnavailableError(
                "GNOME capture needs Fedora's python3-gobject and GStreamer bindings. "
                "Create the virtual environment with --system-site-packages."
            ) from exc
        return Gio, GLib, Gst, GstVideo

    @staticmethod
    def _new_token(prefix: str) -> str:
        """Return a private, valid portal object-path element."""
        return f"chessvision_{prefix}_{secrets.token_hex(12)}"

    @staticmethod
    def _sender_component(unique_name: str) -> str:
        return unique_name.removeprefix(":").replace(".", "_")

    def _expected_request_path(self, token: str) -> str:
        return f"{self.PATH}/request/{self._sender_path}/{token}"

    @staticmethod
    def _options(GLib, **values):
        """Build ``a{sv}`` values without accidentally nesting variants."""
        return values

    def _iterate_until(self, GLib, predicate, timeout_seconds: int = 120) -> None:
        timed_out = {"value": False}
        source = GLib.timeout_source_new_seconds(timeout_seconds)

        def on_timeout(*_args):
            timed_out["value"] = True
            return False

        source.set_callback(on_timeout)
        source.attach(self._context)
        try:
            while not predicate() and not timed_out["value"]:
                self._context.iteration(True)
        finally:
            source.destroy()
        if timed_out["value"]:
            raise CaptureError("Timed out waiting for the GNOME portal response.")

    def _subscribe_response(self, request_path: str, response: dict) -> int:
        def on_response(_connection, _sender, _path, _interface, _signal, parameters, _data):
            code, results = parameters.unpack()
            response["code"] = int(code)
            response["results"] = results
            LOGGER.debug("Portal request completed with response code %d.", int(code))

        return self._bus.signal_subscribe(
            self.PORTAL, self.REQUEST, "Response", request_path, None, 0, on_response, None
        )

    @staticmethod
    def _raise_for_response(code: int) -> None:
        """Translate portal response codes without needing a desktop session."""
        if code == 1:
            raise PortalCancelledError("Screen sharing was cancelled or denied in GNOME.")
        if code != 0:
            raise CaptureError("The XDG desktop portal could not start screen sharing.")

    def _call_request(self, Gio, GLib, method: str, signature: str, values: tuple, token: str) -> dict:
        """Issue a portal request without blocking the GLib dispatch context.

        A subscription is installed before sending the method call. The portal's
        standard request-path convention makes this race-free on current portal
        versions; an older portal's returned path is handled as a fallback.
        """
        expected_path = self._expected_request_path(token)
        response: dict[str, object] = {}
        state: dict[str, object] = {"method_finished": False}
        subscriptions = [self._subscribe_response(expected_path, response)]
        LOGGER.debug("Submitting ScreenCast.%s request.", method)

        def on_complete(connection, result, _user_data):
            try:
                returned_path = connection.call_finish(result).unpack()[0]
            except Exception as exc:
                state["error"] = exc
                LOGGER.warning("ScreenCast.%s D-Bus call failed: %s", method, exc)
                return
            state["method_finished"] = True
            if returned_path != expected_path:
                LOGGER.warning("Portal returned a non-standard request path; updating subscription.")
                self._bus.signal_unsubscribe(subscriptions.pop())
                subscriptions.append(self._subscribe_response(returned_path, response))

        self._bus.call(
            self.PORTAL, self.PATH, self.SCREENCAST, method,
            GLib.Variant(signature, values), None, Gio.DBusCallFlags.NONE, -1, None, on_complete, None,
        )
        try:
            self._iterate_until(
                GLib, lambda: "error" in state or (state["method_finished"] and "code" in response)
            )
        finally:
            for subscription in subscriptions:
                self._bus.signal_unsubscribe(subscription)
        if "error" in state:
            raise CaptureError(f"GNOME portal rejected ScreenCast.{method}: {state['error']}")
        self._raise_for_response(int(response["code"]))
        return response["results"]  # type: ignore[return-value]

    def start(self, on_frame: FrameCallback) -> None:
        Gio, GLib, Gst, GstVideo = self._libraries()
        _require_numpy()
        Gst.init(None)
        self._callback = on_frame
        self._context = GLib.MainContext.new()
        self._context.push_thread_default()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._sender_path = self._sender_component(self._bus.get_unique_name())
            create_token = self._new_token("create")
            session_token = self._new_token("session")
            create_options = self._options(
                GLib,
                handle_token=GLib.Variant("s", create_token),
                session_handle_token=GLib.Variant("s", session_token),
            )
            session = self._call_request(
                Gio, GLib, "CreateSession", "(a{sv})", (create_options,), create_token
            )
            self._session_handle = session["session_handle"]
            select_token = self._new_token("select")
            select_options = self._options(
                GLib,
                handle_token=GLib.Variant("s", select_token),
                types=GLib.Variant("u", 3),
                multiple=GLib.Variant("b", False),
            )
            self._call_request(
                Gio, GLib, "SelectSources", "(oa{sv})",
                (self._session_handle, select_options), select_token,
            )
            start_token = self._new_token("start")
            start_options = self._options(GLib, handle_token=GLib.Variant("s", start_token))
            started = self._call_request(
                Gio, GLib, "Start", "(osa{sv})",
                (self._session_handle, "", start_options), start_token,
            )
        finally:
            self._context.pop_thread_default()
        streams = started.get("streams", [])
        if not streams:
            raise PortalCancelledError("No monitor or window was selected for sharing.")
        node_id = int(streams[0][0])
        result, fd_list = self._bus.call_with_unix_fd_list_sync(
            self.PORTAL, self.PATH, self.SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self._session_handle, {})), None, 0, -1, None, None,
        )
        fd = fd_list.get(result.unpack()[0])
        description = (
            f"pipewiresrc fd={fd} path={node_id} ! videoconvert ! "
            "video/x-raw,format=RGB ! appsink name=framesink emit-signals=true "
            "max-buffers=1 drop=true sync=false"
        )
        self._pipeline = Gst.parse_launch(description)
        self._pipeline.get_by_name("framesink").connect(
            "new-sample", self._on_sample, Gst, GstVideo
        )
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise CaptureError("PipeWire failed to start the approved portal stream.")

    def _on_sample(self, sink, Gst, GstVideo):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        caps = sample.get_caps().get_structure(0)
        width = caps.get_value("width")
        height = caps.get_value("height")
        buffer = sample.get_buffer()
        success, mapped = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            video_meta = GstVideo.buffer_get_video_meta(buffer)
            stride = video_meta.stride[0] if video_meta is not None else width * 3
            frame = frame_from_rgb_buffer(mapped.data, width, height, stride)
            if self._callback is not None:
                self._callback(frame)
        finally:
            buffer.unmap(mapped)
        return Gst.FlowReturn.OK

    def stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(0)  # Gst.State.NULL without requiring GI at import time.
            self._pipeline = None
        if self._session_handle and self._bus is not None:
            try:
                self._bus.call_sync(
                    self.PORTAL, self._session_handle, "org.freedesktop.portal.Session", "Close",
                    None, None, 0, -1, None,
                )
            except Exception:
                pass
        self._session_handle = None
        self._callback = None
