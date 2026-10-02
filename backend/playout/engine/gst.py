"""GStreamer bootstrap shared by the engine modules."""
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstMpegts", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GLib, Gst, GstMpegts, GstVideo  # noqa: E402

Gst.init(None)
GstMpegts.initialize()

# Every element the engine uses -> the Ubuntu/Debian package that ships it.
REQUIRED_ELEMENTS = {
    "uridecodebin": "gstreamer1.0-plugins-base", "appsrc": "gstreamer1.0-plugins-base",
    "appsink": "gstreamer1.0-plugins-base", "videoconvert": "gstreamer1.0-plugins-base",
    "videoscale": "gstreamer1.0-plugins-base", "videorate": "gstreamer1.0-plugins-base",
    "audioconvert": "gstreamer1.0-plugins-base", "audioresample": "gstreamer1.0-plugins-base",
    "videotestsrc": "gstreamer1.0-plugins-base", "audiotestsrc": "gstreamer1.0-plugins-base",
    "textoverlay": "gstreamer1.0-x", "clockoverlay": "gstreamer1.0-x",
    "deinterlace": "gstreamer1.0-plugins-good", "jpegenc": "gstreamer1.0-plugins-good",
    "aacparse": "gstreamer1.0-plugins-good", "udpsrc": "gstreamer1.0-plugins-good",
    "udpsink": "gstreamer1.0-plugins-good",
    "h264parse": "gstreamer1.0-plugins-bad", "mpegtsmux": "gstreamer1.0-plugins-bad",
    "tsdemux": "gstreamer1.0-plugins-bad", "srtsrc": "gstreamer1.0-plugins-bad",
    "srtsink": "gstreamer1.0-plugins-bad",
    "x264enc": "gstreamer1.0-plugins-ugly", "avenc_aac": "gstreamer1.0-libav",
    "avdec_h264": "gstreamer1.0-libav",
}


def check_elements() -> None:
    """Fail fast with an actionable message if a GStreamer plugin is missing."""
    missing = {name: pkg for name, pkg in REQUIRED_ELEMENTS.items() if Gst.ElementFactory.find(name) is None}
    if missing:
        pkgs = " ".join(sorted(set(missing.values())))
        raise RuntimeError(f"missing GStreamer elements: {', '.join(missing)}. Install: apt install {pkgs}")


__all__ = ["GLib", "Gst", "GstMpegts", "GstVideo", "check_elements"]
