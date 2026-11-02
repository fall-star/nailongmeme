from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import convert_formats


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_frames(size=(12, 12)):
    return [Image.new("RGB", size, (index * 80, 20, 20)) for index in range(3)]


class ConversionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="meme-convert-test-")
        self.root = Path(self.tempdir.name)
        self.src = self.root / "raw"
        self.out = self.root / "converted"
        self.src.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _png_alpha(self, name: str = "alpha.png") -> Path:
        image = Image.new("RGBA", (10, 10), (255, 0, 0, 0))
        for x in range(3, 7):
            for y in range(3, 7):
                image.putpixel((x, y), (255, 0, 0, 255))
        path = self.src / name
        image.save(path, format="PNG")
        return path

    def _static_jpeg(self, name: str = "photo.jpg") -> Path:
        path = self.src / name
        Image.new("RGB", (20, 10), (10, 200, 30)).save(path, format="JPEG", quality=90)
        return path

    def _static_webp(self, name: str = "still.webp") -> Path:
        path = self.src / name
        Image.new("RGB", (20, 10), (0, 100, 250)).save(path, format="WEBP")
        return path

    def _animated_gif(self, name: str = "anim.gif") -> Path:
        path = self.src / name
        frames = _make_frames()
        frames[0].save(
            path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[50, 100, 150],
            loop=2,
        )
        return path

    def _single_gif(self, name: str = "single.gif") -> Path:
        path = self.src / name
        Image.new("RGB", (8, 8), (20, 20, 20)).save(path, format="GIF")
        return path

    def _animated_webp(self, name: str = "anim.webp") -> Path:
        path = self.src / name
        frames = _make_frames()
        frames[0].save(
            path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=[40, 80, 120],
            loop=1,
        )
        return path

    def _animated_png(self, name: str = "anim.png") -> Path:
        path = self.src / name
        frames = _make_frames()
        frames[0].save(
            path,
            format="PNG",
            save_all=True,
            append_images=frames[1:],
            duration=[40, 80, 120],
            loop=1,
        )
        return path

    def test_mixed_conversion_and_manifest(self) -> None:
        alpha = self._png_alpha("1.png")
        animated_gif = self._animated_gif("2.gif")
        jpeg = self._static_jpeg("3.jpg")
        webp = self._static_webp("4.webp")
        single_gif = self._single_gif("5.gif")
        animated_webp = self._animated_webp("6.webp")
        animated_png = self._animated_png("7.png")
        report = convert_formats.run_conversion(
            self.src, self.out, workers=2, jpeg_quality=95
        )
        self.assertEqual(report["counts"]["failed"], 0)
        self.assertEqual(report["counts"]["total"], 7)
        self.assertEqual(report["counts"]["static_jpg"], 4)
        self.assertEqual(report["counts"]["animated_gif"], 3)
        self.assertEqual(report["counts"]["copied_gif"], 1)
        self.assertEqual(report["counts"]["copied_jpeg"], 1)

        expected = {
            "1.jpg",
            "2.gif",
            "3.jpg",
            "4.jpg",
            "5.jpg",
            "6.gif",
            "7.gif",
        }
        self.assertEqual(
            {path.name for path in self.out.iterdir() if path.suffix != ".json"},
            expected,
        )
        self.assertTrue((self.out / "conversion-manifest.json").exists())
        manifest = json.loads((self.out / "conversion-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["items"]), 7)
        self.assertEqual(manifest["counts"]["failed"], 0)

        # Animated GIFs are copied byte-for-byte for identical playback.
        self.assertEqual(_sha(animated_gif), _sha(self.out / "2.gif"))
        with Image.open(self.out / "2.gif") as image:
            self.assertEqual(getattr(image, "n_frames", 1), 3)
            durations = []
            for index in range(3):
                image.seek(index)
                durations.append(image.info.get("duration"))
            self.assertEqual(durations, [50, 100, 150])
            self.assertEqual(image.info.get("loop"), 2)

        # Static JPEG is copied losslessly by default.
        self.assertEqual(_sha(jpeg), _sha(self.out / "3.jpg"))

        # Static PNG/WebP/single-frame GIF become JPEG.
        for name in ("1.jpg", "3.jpg", "4.jpg", "5.jpg"):
            with Image.open(self.out / name) as image:
                self.assertEqual(image.format, "JPEG")

        # Animated WebP/APNG become GIF with frame count, durations and loop preserved.
        for name, source in (("6.gif", animated_webp), ("7.gif", animated_png)):
            with Image.open(self.out / name) as image:
                self.assertEqual(image.format, "GIF")
                self.assertEqual(getattr(image, "n_frames", 1), 3)
                durations = []
                for index in range(3):
                    image.seek(index)
                    durations.append(image.info.get("duration"))
                self.assertEqual(durations, [40, 80, 120])
                self.assertEqual(image.info.get("loop"), 1)

        # Transparent static alpha is composited onto white.
        with Image.open(self.out / "1.jpg") as image:
            corner = image.convert("RGB").getpixel((0, 0))
            center = image.convert("RGB").getpixel((5, 5))
        self.assertTrue(all(channel > 245 for channel in corner), corner)
        self.assertGreater(center[0], 200)
        self.assertLess(center[1], 80)

        # Second run is idempotent.
        second = convert_formats.run_conversion(self.src, self.out, workers=2)
        self.assertEqual(second["counts"]["converted"], 0)
        self.assertEqual(second["counts"]["skipped"], 7)
        self.assertEqual(second["counts"]["failed"], 0)

    def test_dry_run_writes_nothing(self) -> None:
        self._png_alpha("a.png")
        self._animated_gif("b.gif")
        report = convert_formats.run_conversion(self.src, self.out, dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["static"], 1)
        self.assertEqual(report["animated"], 1)
        self.assertFalse(self.out.exists())

    def test_force_reconverts(self) -> None:
        self._static_webp("a.webp")
        first = convert_formats.run_conversion(self.src, self.out)
        self.assertEqual(first["counts"]["converted"], 1)
        second = convert_formats.run_conversion(self.src, self.out)
        self.assertEqual(second["counts"]["skipped"], 1)
        third = convert_formats.run_conversion(self.src, self.out, force=True)
        self.assertEqual(third["counts"]["converted"], 1)

    def test_output_inside_source_is_rejected(self) -> None:
        self._static_webp("a.webp")
        with self.assertRaises(convert_formats.ConversionError):
            convert_formats.run_conversion(self.src, self.src / "out")


if __name__ == "__main__":
    unittest.main()
