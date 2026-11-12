""" Viewport capture commands (get_viewport_screenshot).

The same wire command also carries the two pause-safe read-only payloads that
must keep working while the human has paused AI operations:

* `spec`     - run the shared-kernel view capture (runtime/py/render/view.py)
                without executing arbitrary code;
* `presence` - install one presence snapshot (presence_draw.set_presence).

Keeping them on a command that already starts with `get_` leaves the command
count unchanged and never re-opens arbitrary execute_code during a pause.
"""

import json
import sys

import bpy

KERNEL_CODES = ("kernel_not_loaded", "capture_failed", "invalid_spec")


def _kernel_view_api():
    """Return the shared kernel view API, or None when the kernel is not loaded."""
    kernel = sys.modules.get("dsh_rt_kernel")
    if kernel is None:
        return None
    return getattr(kernel, "dsh_view_api", None)


def _payload(value):
    """Accept a mapping or a JSON object string; anything else is None."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _area_index_for(area_types, area_index):
    """Index of the requested editor; None means the 3D viewport, never areas[0].

    areas[0] is usually the PROPERTIES editor, so a full=true capture without an
    explicit area used to return the wrong editor. Returns -1 when out of range.
    """
    if not area_types:
        return -1
    if area_index is None:
        return next((i for i, kind in enumerate(area_types) if kind == "VIEW_3D"), 0)
    try:
        index = int(area_index)
    except (TypeError, ValueError):
        return -1
    return index if 0 <= index < len(area_types) else -1


class ScreenshotCommands:
    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png",
                                spec=None, presence=None, area=None):
        """
        Capture a screenshot of the current 3D viewport and save it to the specified path.

        Parameters:
        - max_size: Maximum size in pixels for the largest dimension of the image
        - filepath: Path where to save the screenshot file
        - format: Image format (png, jpg, etc.)
        - spec: pause-safe view-capture JSON handled by the shared kernel
        - presence: pause-safe presence snapshot (JSON object or object string)
        - area: pause-safe full-window screenshot of one screen area (see full=true)

        Returns success/error status
        """
        if presence is not None:
            return self._apply_presence(presence)
        if spec is not None:
            return self._readonly_capture(spec)
        if area == "window":
            return self._window_screenshot(filepath, format, max_size)
        if area is not None:
            return self._area_screenshot(area, filepath, format, max_size)
        # screen.screenshot_area captures the OS window framebuffer, which is
        # all-black whenever the Blender window is not composited in the
        # foreground (the normal case when Blender is driven headless-style via
        # the DSH plugin). Render the viewport with gpu.types.GPUOffScreen.draw_view3d
        # instead, which is independent of window compositing state, and fall
        # back to the window grab if offscreen rendering is unavailable (e.g. no
        # GPU context). The response reports which path produced the image.
        try:
            if not filepath:
                return {"error": "No filepath provided"}

            area = region = space = None
            for a in bpy.context.screen.areas:
                if a.type == 'VIEW_3D':
                    area = a
                    space = a.spaces.active
                    region = next((r for r in a.regions if r.type == 'WINDOW'), None)
                    break

            if not area or region is None or space is None:
                return {"error": "No 3D viewport found"}

            method = "offscreen"
            try:
                import gpu
                import numpy as np

                r3d = space.region_3d
                src_w, src_h = region.width, region.height
                if max(src_w, src_h) > max_size:
                    s = max_size / max(src_w, src_h)
                    width, height = max(1, int(src_w * s)), max(1, int(src_h * s))
                else:
                    width, height = src_w, src_h

                offscreen = gpu.types.GPUOffScreen(width, height)  # pyright: ignore[reportCallIssue]
                try:
                    offscreen.draw_view3d(
                        bpy.context.scene, bpy.context.view_layer, space, region,  # pyright: ignore[reportArgumentType]
                        r3d.view_matrix, r3d.window_matrix, do_color_management=True,
                    )
                    buf = offscreen.texture_color.read()
                finally:
                    offscreen.free()

                buf.dimensions = width * height * 4
                pixels = np.asarray(buf, dtype=np.float32) / 255.0  # GPU buffer is 0..255

                image = bpy.data.images.new("bridge_viewport", width, height, alpha=True)
                image.pixels.foreach_set(pixels.ravel())
                image.filepath_raw = filepath
                image.file_format = format.upper()
                image.save()
                bpy.data.images.remove(image)

            except Exception as offscreen_err:
                print(f"[DSH Blender Bridge] offscreen capture failed ({offscreen_err}); "
                      "falling back to window grab", flush=True)
                method = "window_grab"
                with bpy.context.temp_override(area=area):
                    bpy.ops.screen.screenshot_area(filepath=filepath)
                img = bpy.data.images.load(filepath)
                width, height = img.size
                if max(width, height) > max_size:
                    s = max_size / max(width, height)
                    width, height = int(width * s), int(height * s)
                    img.scale(width, height)
                    img.file_format = format.upper()
                    img.save()
                bpy.data.images.remove(img)

            return {
                "success": True,
                "width": width,
                "height": height,
                "filepath": filepath,
                "method": method,
            }

        except Exception as e:
            return {"error": str(e)}

    def _apply_presence(self, presence):
        """Install a presence snapshot without executing arbitrary code."""
        payload = _payload(presence)
        if payload is None:
            return {"error": "presence payload must be a JSON object", "code": "invalid_presence"}
        try:
            from ... import presence_draw
            presence_draw.set_presence(payload)
        except Exception as exc:
            return {"error": "presence update failed: %s" % str(exc), "code": "presence_failed"}
        return {"success": True, "presence": True}

    def _window_screenshot(self, filepath, image_format, max_size=1024):
        """Whole-window capture (full=true): screen.screenshot keeps every editor.

        The framebuffer is only complete while the window is composited, so the
        receipt says which path produced it; callers that need a trustworthy
        image use viewpoint=custom/camera instead.
        """
        if not filepath:
            return {"error": "No filepath provided"}
        try:
            limit = max(1, int(max_size or 1024))
            window = bpy.context.window
            if window is None:
                return {"error": "No window found for the current screen"}
            with bpy.context.temp_override(window=window):
                bpy.ops.screen.screenshot(filepath=filepath)
            image = bpy.data.images.load(filepath)
            width, height = image.size
            if max(width, height) > limit:
                scale = limit / max(width, height)
                width, height = max(1, int(width * scale)), max(1, int(height * scale))
                image.scale(width, height)
                image.file_format = image_format.upper()
                image.save()
            bpy.data.images.remove(image)
            return {
                "success": True,
                "width": int(width),
                "height": int(height),
                "filepath": filepath,
                "method": "screenshot_window",
                "area": "window",
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _area_screenshot(self, area_index, filepath, image_format, max_size=1024):
        """Full-window capture of one screen area (pause-safe: started with get_).

        The window grab is resized to max_size on its longest edge: the see spec
        requires a fixed 1024-pixel longest edge for full=true, and the raw
        framebuffer is usually much larger than that.
        """
        if not filepath:
            return {"error": "No filepath provided"}
        try:
            screen = bpy.context.screen
            areas = list(screen.areas)
            if not areas:
                return {"error": "No areas found in the current screen"}
            limit = max(1, int(max_size or 1024))
            index = _area_index_for([a.type for a in areas], area_index)
            if index < 0:
                return {"error": "area %s is out of range (0..%d)"
                        % (area_index, len(areas) - 1),
                        "code": "invalid_area"}
            target = areas[index]
            window = next(
                (w for w in bpy.context.window_manager.windows if w.screen == screen), None)
            if window is None:
                return {"error": "No window found for the current screen"}
            with bpy.context.temp_override(window=window, area=target):
                bpy.ops.screen.screenshot_area(filepath=filepath)
            image = bpy.data.images.load(filepath)
            width, height = image.size
            if max(width, height) > limit:
                scale = limit / max(width, height)
                width, height = max(1, int(width * scale)), max(1, int(height * scale))
                image.scale(width, height)
                image.file_format = image_format.upper()
                image.save()
            bpy.data.images.remove(image)
            return {
                "success": True,
                "width": int(width),
                "height": int(height),
                "filepath": filepath,
                "method": "screenshot_area",
                "area": index,
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _readonly_capture(self, spec):
        """Run the view capture that lives in the shared kernel (no new code path)."""
        payload = _payload(spec)
        if payload is None:
            return {"ok": False, "code": "invalid_spec",
                    "error": "capture spec must be a JSON object"}
        api = _kernel_view_api()
        if api is None:
            return {"ok": False, "code": "kernel_not_loaded",
                    "error": "view capture needs the shared kernel; resume AI operations once and retry"}
        try:
            return api["capture"](json.dumps(payload))
        except Exception as exc:
            return {"ok": False, "code": "capture_failed",
                    "error": "view capture failed: %s" % str(exc)}
