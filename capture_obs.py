"""OBS WebSocket (v5, OBS 28+) capture layer.

Uses ReqClient.send(..., raw=True) everywhere so we depend on the raw protocol
request names, not on obsws-python's wrapper method signatures.
"""
import base64
import io

from PIL import Image


class ObsCapture:
    def __init__(self, host="localhost", port=4455, password="", timeout=5):
        # imported here: OBS support is optional (the packaged app leaves it out, GPL-3.0 library)
        import obsws_python as obs
        self.client = obs.ReqClient(host=host, port=port, password=password or None,
                                    timeout=timeout)

    def version(self):
        return self.client.send("GetVersion", raw=True)

    def inputs(self):
        return self.client.send("GetInputList", raw=True)["inputs"]

    def screenshot(self, source):
        """Native-resolution PNG of one source, as a PIL RGB image."""
        resp = self.client.send("GetSourceScreenshot",
                                {"sourceName": source, "imageFormat": "png"}, raw=True)
        data = resp["imageData"]                     # "data:image/png;base64,...."
        b64 = data.split(",", 1)[1] if data.startswith("data:") else data
        return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")

    def set_text(self, input_name, text):
        self.client.send("SetInputSettings",
                         {"inputName": input_name, "inputSettings": {"text": text}}, raw=True)

    def close(self):
        try:
            self.client.disconnect()
        except Exception:
            pass


def from_config(cfg):
    o = cfg["obs"]
    return ObsCapture(o.get("host", "localhost"), o.get("port", 4455),
                      o.get("password", ""), o.get("timeout", 5))


def crop(img, box, expected_res=None, anchor=None, name=None):
    """Crop [x1, y1, x2, y2] (in config pixels). If the frame size differs, the box is moved
    by its anchor ("bottom-center", "minimap", "top-left") or scaled (no anchor), unless
    layouts.json has a recorded box for region `name` at this frame size; see layout.py."""
    from layout import place
    return img.crop(tuple(place(img, box, expected_res, anchor, name)))
