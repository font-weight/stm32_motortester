"""HOST-001 H01/H02/H06: эмулятор → raw → файл → открытие → SVG/CSV.

Ожидания точек/единиц заданы независимо. Синтетические 20 окон проверяют
программу, а не опыт LV-001, native USB, Windows или готовность MVP.
"""
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from motortester.emulator import demo_message, from_message
from motortester.model import Capture, CaptureError
from motortester.plotting import save_svg, svg
from motortester.recording import encode, export_csv, load, save


SESSION = "17d280f6-bd69-4df4-aa98-651c09d06f9b"
SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


def reference_capture(**changes):
    values = dict(samples=(0, 4095, 1024, 3072, 17), session_id=SESSION,
                  sequence=3, nominal_fs_hz=50_000,
                  received_at_utc="2026-10-10T11:00:00Z", origin="simulator",
                  trigger_kind="external", trigger_index=2, pretrigger_samples=2,
                  sensor="Контрольный синтетический массив")
    values.update(changes)
    return Capture(**values)


def parse_svg(capture):
    return ET.fromstring(svg(capture))


def graph_points(document):
    line = document.find(".//svg:polyline", SVG_NS)
    if line is None:
        raise AssertionError("На SVG отсутствует график исходных точек")
    return [tuple(map(float, pair.split(","))) for pair in line.attrib["points"].split()]


def labels(document):
    return " ".join("".join(node.itertext()) for node in document.findall(".//svg:text", SVG_NS))


class EmulatorInputTests(unittest.TestCase):
    def test_profile_is_explicitly_synthetic_and_nominal(self):
        before = datetime.now(timezone.utc)
        message = demo_message(sequence=19, session_id=SESSION)
        after = datetime.now(timezone.utc)
        capture = from_message(message)
        self.assertEqual(capture.session_id, SESSION)
        self.assertEqual(capture.sequence, 19)
        self.assertEqual(capture.origin, "simulator")
        self.assertIn("synthetic", capture.sensor.lower())
        self.assertEqual(capture.nominal_fs_hz, 50_000)
        self.assertEqual(len(capture.samples), 2048)
        self.assertIs(type(capture.samples), tuple)
        self.assertTrue(all(type(value) is int and 0 <= value <= 4095
                            for value in capture.samples))
        self.assertEqual(capture.trigger_index, 512)
        self.assertEqual(capture.pretrigger_samples, 512)
        self.assertEqual(capture.trigger_kind, "signal")
        self.assertEqual(capture.trigger_edge, "rising")
        self.assertEqual(capture.trigger_level_code, 2048)
        self.assertIsNone(capture.firmware_version)
        self.assertIsNone(capture.protocol_version)
        stamp = datetime.fromisoformat(capture.received_at_utc.replace("Z", "+00:00"))
        self.assertEqual(stamp.utcoffset().total_seconds(), 0)
        self.assertLessEqual(before, stamp)
        self.assertLessEqual(stamp, after)
        # Четверти периода эталонной синусоиды: без вызова формулы генератора.
        self.assertEqual(tuple(capture.samples[i] for i in (512, 537, 562, 587, 612)),
                         (2048, 3048, 2048, 1048, 2048))

    def test_next_message_does_not_mutate_previous_capture(self):
        message = demo_message(sequence=0, session_id=SESSION)
        capture = from_message(message)
        before = encode(capture)
        message["samples"] = (0, 4095)
        message["sequence"] = 900
        next_capture = from_message(demo_message(sequence=1, session_id=SESSION))
        self.assertEqual(encode(capture), before)
        self.assertEqual(capture.sequence, 0)
        self.assertEqual(next_capture.sequence, 1)
        self.assertEqual(len(capture.samples), 2048)

    def test_missing_unknown_and_wrong_type_event_version_is_rejected(self):
        for version in (None, True, 1.0, "1", 0, 2):
            message = demo_message(session_id=SESSION)
            message["event_version"] = version
            with self.subTest(version=version), self.assertRaises(CaptureError):
                from_message(message)
        message = demo_message(session_id=SESSION)
        del message["event_version"]
        with self.assertRaises(CaptureError):
            from_message(message)
        for value in (None, [], "message", 1):
            with self.subTest(value=value), self.assertRaises(CaptureError):
                from_message(value)

    def test_missing_required_and_unrecognized_event_fields_are_rejected(self):
        for field in ("samples", "session_id", "sequence", "nominal_fs_hz", "received_at_utc"):
            message = demo_message(session_id=SESSION)
            del message[field]
            with self.subTest(field=field), self.assertRaises(CaptureError):
                from_message(message)
        message = demo_message(session_id=SESSION)
        message["unexpected"] = True
        with self.assertRaises(CaptureError):
            from_message(message)

    def test_raw_values_and_metadata_are_not_silently_coerced(self):
        bad_values = {"samples": [(0.5,) * 2048, (True,) * 2048, (-1,) * 2048,
                                   (4096,) * 2048, [2048] * 2048],
                      "sequence": [True, "0", 0.0],
                      "nominal_fs_hz": [True, "50000", 10**400],
                      "received_at_utc": [None, "2026-10-10T11:00:00"],
                      "trigger_index": [True, "512", 512.0]}
        for field, values in bad_values.items():
            for value in values:
                message = demo_message(session_id=SESSION)
                message[field] = value
                with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(CaptureError):
                    from_message(message)

    def test_invalid_supplied_session_is_not_replaced_with_a_fresh_identity(self):
        for session in ("", 0, False, []):
            with self.subTest(session=session), self.assertRaises(CaptureError):
                from_message(demo_message(session_id=session))


class FirstPathTests(unittest.TestCase):
    def test_twenty_windows_survive_disk_reopen_and_exports_unchanged(self):
        identities = set()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for sequence in range(20):
                with self.subTest(sequence=sequence):
                    capture = from_message(demo_message(sequence, SESSION))
                    raw = capture.samples
                    path = root / f"окно-{sequence:02d}.mtcap"
                    save(path, capture)
                    reopened = load(path)
                    self.assertIsNot(reopened, capture)
                    self.assertEqual(reopened, capture)
                    self.assertEqual(reopened.samples, raw)
                    self.assertEqual(len(reopened.samples), 2048)
                    identities.add((reopened.session_id, reopened.sequence))

                    # Оба экспорта получают именно запись, заново открытую с диска.
                    before = encode(reopened)
                    svg_path = path.with_suffix(".svg")
                    csv_path = path.with_suffix(".csv")
                    save_svg(svg_path, reopened)
                    export_csv(csv_path, reopened)
                    document = ET.parse(svg_path).getroot()
                    points = graph_points(document)
                    self.assertEqual(len(points), 2048)
                    self.assertIn("синтетические", labels(document).lower())
                    with csv_path.open(encoding="utf-8", newline="") as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertEqual([int(row["adc_code"]) for row in rows], list(raw))
                    self.assertEqual({row["origin"] for row in rows}, {"simulator"})
                    self.assertEqual({int(row["window_sequence"]) for row in rows}, {sequence})
                    self.assertEqual(encode(reopened), before)
                    self.assertEqual(path.read_bytes(), before)
            self.assertEqual(identities, {(SESSION, sequence) for sequence in range(20)})
            self.assertEqual(len(list(root.glob("*.mtcap"))), 20)
            self.assertEqual(len(list(root.glob("*.svg"))), 20)
            self.assertEqual(len(list(root.glob("*.csv"))), 20)

    def test_a_fresh_python_process_can_open_the_saved_recording(self):
        capture = reference_capture()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "проверка.mtcap"
            save(path, capture)
            host = Path(__file__).resolve().parents[1]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(host)
            script = ("import json,sys; from motortester.recording import load; "
                      "c=load(sys.argv[1]); print(json.dumps([list(c.samples), "
                      "c.session_id, c.sequence, c.origin, c.received_at_utc]))")
            result = subprocess.run([sys.executable, "-c", script, str(path)],
                                    cwd=temporary, env=env, text=True,
                                    capture_output=True, check=False, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                             [list(capture.samples), SESSION, 3, "simulator",
                              "2026-10-10T11:00:00Z"])


class SvgRequirementTests(unittest.TestCase):
    def test_svg_is_xml_and_points_preserve_order_scale_and_full_extent(self):
        capture = reference_capture()
        original = encode(capture)
        document = parse_svg(capture)
        self.assertEqual(document.tag, "{http://www.w3.org/2000/svg}svg")
        points = graph_points(document)
        self.assertEqual(len(points), 5)
        # Заявленный прямоугольник графика: x=90…1060, y=100…460.
        expected = [(90.0, 460.0), (332.5, 100.0),
                    (575.0, 460 - 360 * 1024 / 4095),
                    (817.5, 460 - 360 * 3072 / 4095),
                    (1060.0, 460 - 360 * 17 / 4095)]
        for actual, wanted in zip(points, expected):
            self.assertAlmostEqual(actual[0], wanted[0], delta=0.00051)
            self.assertAlmostEqual(actual[1], wanted[1], delta=0.00051)
            self.assertTrue(all(math.isfinite(value) for value in actual))
            self.assertGreaterEqual(actual[0], 90)
            self.assertLessEqual(actual[0], 1060)
            self.assertGreaterEqual(actual[1], 100)
            self.assertLessEqual(actual[1], 460)
        self.assertEqual(encode(capture), original)

    def test_svg_labels_use_adc_codes_nominal_time_and_explicit_synthetic_source(self):
        text = labels(parse_svg(reference_capture()))
        self.assertIn("Код АЦП", text)
        self.assertIn("Время, мс", text)
        self.assertIn("номинальной", text)
        self.assertIn("точность не проверена", text)
        self.assertIn("синтетические данные", text)
        self.assertIn("ДЕМОНСТРАЦИЯ", text)
        self.assertIn("исходные данные в .mtcap", text)
        self.assertNotIn("кВ", text)

    def test_trigger_mark_matches_index_and_timeout_does_not_invent_event(self):
        document = parse_svg(reference_capture())
        markers = [node for node in document.findall(".//svg:path", SVG_NS)
                   if "stroke-dasharray" in node.attrib]
        self.assertEqual(len(markers), 1)
        self.assertTrue(markers[0].attrib["d"].startswith("M575.0 "))
        self.assertIn("Запуск", labels(document))
        timeout = reference_capture(trigger_kind="auto_timeout", trigger_index=None,
                                    pretrigger_samples=0)
        document = parse_svg(timeout)
        self.assertNotIn("Запуск", labels(document))
        self.assertEqual([node for node in document.findall(".//svg:path", SVG_NS)
                          if "stroke-dasharray" in node.attrib], [])

    def test_2048_sample_time_extent_is_40_94_ms_not_40_96_ms(self):
        capture = from_message(demo_message(session_id=SESSION))
        self.assertAlmostEqual(capture.time_seconds(0), -0.01024)
        self.assertAlmostEqual(capture.time_seconds(2047), 0.03070)
        self.assertAlmostEqual((capture.time_seconds(2047) - capture.time_seconds(0)) * 1000,
                               40.94)
        document = parse_svg(capture)
        text_values = ["".join(node.itertext()) for node in document.findall(".//svg:text", SVG_NS)]
        self.assertIn("-10.240", text_values)
        self.assertIn("30.700", text_values)
        self.assertNotIn("40.960", text_values)
        self.assertEqual(graph_points(document)[0][0], 90.0)
        self.assertEqual(graph_points(document)[-1][0], 1060.0)

    def test_boundary_codes_and_quality_flags_are_visible(self):
        text = labels(parse_svg(reference_capture(quality_flags=("quality_check_required",))))
        self.assertIn("quality_check_required", text)
        self.assertRegex(text.lower(), r"крайн|границ|перегруз")
        self.assertRegex(text.lower(), r"возмож|подозр|предупрежд")

    def test_xml_special_characters_in_visible_metadata_are_escaped(self):
        flag = "test<&\"'>flag"
        document = parse_svg(reference_capture(quality_flags=(flag,)))
        self.assertIn(flag, labels(document))

    def test_one_sample_has_a_visible_point_and_finite_coordinates(self):
        document = parse_svg(reference_capture(samples=(2048,), trigger_index=0,
                                                pretrigger_samples=0))
        points = graph_points(document)
        self.assertEqual(len(points), 1)
        self.assertTrue(all(math.isfinite(value) for value in points[0]))
        circles = document.findall(".//svg:circle", SVG_NS)
        self.assertTrue(any(float(circle.attrib.get("r", "0")) > 0 for circle in circles),
                        "Линия из одной вершины невидима; нужен маркер точки")

    def test_svg_save_has_no_clobber_default_and_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "график.svg"
            path.write_bytes(b"old svg")
            with self.assertRaises(FileExistsError):
                save_svg(path, reference_capture())
            self.assertEqual(path.read_bytes(), b"old svg")
            save_svg(path, reference_capture(), overwrite=True)
            document = ET.parse(path).getroot()
            self.assertEqual(len(graph_points(document)), 5)
            self.assertEqual(list(Path(temporary).glob(".motortester-*")), [])


if __name__ == "__main__":
    unittest.main()
