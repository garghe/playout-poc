"""GStreamer bootstrap shared by the engine modules."""
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstMpegts", "1.0")
from gi.repository import GLib, Gst, GstMpegts  # noqa: E402

Gst.init(None)
GstMpegts.initialize()

__all__ = ["GLib", "Gst", "GstMpegts"]
