"""Offline SVG illustration; full raw capture remains in the .mtcap file."""
from html import escape
from textwrap import wrap
from .model import Capture
from .recording import atomic_write


def svg(capture: Capture) -> str:
    warnings = list(capture.quality_flags)
    if capture.rail_warning:
        warnings.append("крайние коды АЦП: возможное ограничение")
    quality = "; ".join(warnings) or "флаги качества отсутствуют; это не доказательство точности"
    quality_lines = wrap(quality, width=120)
    width, height, left, top, plot_w, plot_h = 1100, 600 + 18 * (len(quality_lines) - 1), 90, 100, 970, 360
    n = len(capture.samples)
    def x(i):
        return left + i * plot_w / max(1, n - 1)
    def y(code):
        return top + plot_h * (1 - code / 4095)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             f'<rect width="{width}" height="{height}" fill="#101723"/>',
             '<g fill="#e4ebf5" font-family="DejaVu Sans,Arial,sans-serif">',
             '<text x="32" y="35" font-size="23">Мотортестер · исходные отсчёты</text>']
    label = "ДЕМОНСТРАЦИЯ · синтетические данные" if capture.origin == "simulator" else "ЗАПИСЬ ПРИБОРА · шкалы не калиброваны"
    parts.append(f'<text x="32" y="65" fill="#f5bf65" font-size="15">{label} · {n} отсчётов · Fs ном. {capture.nominal_fs_hz:g} Гц</text>')
    for step in range(11):
        pos = left + step * plot_w / 10
        parts.append(f'<path d="M{pos} {top}v{plot_h}" stroke="#2a3545"/>')
    for step in range(5):
        code = step * 4095 / 4
        pos = y(code)
        parts.append(f'<path d="M{left} {pos}h{plot_w}" stroke="#2a3545"/><text x="75" y="{pos+5}" text-anchor="end" font-size="12">{code:.0f}</text>')
    points = " ".join(f"{x(i):.3f},{y(v):.3f}" for i, v in enumerate(capture.samples))
    parts.append(f'<polyline points="{points}" fill="none" stroke="#63d5be" stroke-width="1.5"/>')
    if n == 1:
        parts.append(f'<circle cx="{x(0)}" cy="{y(capture.samples[0])}" r="3" fill="#63d5be"/>')
    if capture.trigger_index is not None:
        pos = x(capture.trigger_index)
        parts.append(f'<path d="M{pos} {top}v{plot_h}" stroke="#f5bf65" stroke-dasharray="5 5"/><text x="{pos+6}" y="{top+17}" font-size="12">Запуск</text>')
    for i in (0, n-1):
        parts.append(f'<text x="{x(i)}" y="485" text-anchor="middle" font-size="12">{capture.time_seconds(i)*1000:.3f}</text>')
    for row, line in enumerate(quality_lines):
        parts.append(f'<text x="32" y="{578 + row * 18}" fill="#f5bf65" font-size="12">{escape(line)}</text>')
    trigger_text = {"signal": "запуск по сигналу", "external": "внешний запуск", "auto_timeout": "тайм-аут без события запуска"}[capture.trigger_kind]
    parts.extend(['<text x="24" y="90" font-size="12">Код АЦП</text>',
                  '<text x="575" y="515" text-anchor="middle" font-size="14">Время, мс · по номинальной Fs, точность не проверена</text>',
                  f'<text x="32" y="549" font-size="12">Окно {capture.sequence} · {trigger_text} · иллюстрация, исходные данные в .mtcap</text>', '</g></svg>'])
    return "\n".join(parts)


def save_svg(path, capture: Capture, *, overwrite=False):
    atomic_write(path, svg(capture).encode("utf-8"), overwrite=overwrite)
