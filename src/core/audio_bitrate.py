"""
Битрейт MP3 по TTS-движкам: что поддерживается и аргументы ffmpeg.
"""

from __future__ import annotations

from typing import Dict, List

# Поддерживаемые значения (kbps) для каждого бэкенда.
# Edge: Microsoft жёстко отдаёт audio-24khz-48kbitrate-mono-mp3.
TTS_BITRATE_OPTIONS_KBPS: Dict[str, List[int]] = {
    "edge": [48],
    "piper": [48, 64, 96, 128, 192],
    "silero": [48, 64, 96, 128, 192],
    "supertonic": [48, 64, 96, 128, 192],
}

TTS_BITRATE_DEFAULT_KBPS: Dict[str, int] = {
    "edge": 48,
    "piper": 128,
    "silero": 128,
    "supertonic": 128,
}


def supported_bitrates(backend: str) -> List[int]:
    """Список доступных битрейтов (kbps) для движка."""
    return list(TTS_BITRATE_OPTIONS_KBPS.get(backend, TTS_BITRATE_OPTIONS_KBPS["edge"]))


def default_bitrate(backend: str) -> int:
    """Битрейт по умолчанию для движка."""
    return TTS_BITRATE_DEFAULT_KBPS.get(backend, 128)


def clamp_bitrate(backend: str, kbps: int) -> int:
    """Привести выбранный битрейт к допустимому для движка."""
    options = supported_bitrates(backend)
    if not options:
        return 48
    if kbps in options:
        return kbps
    # ближайший снизу, иначе дефолт
    lower = [o for o in options if o <= kbps]
    if lower:
        return max(lower)
    return default_bitrate(backend)


def bitrate_menu_labels(backend: str, lang: str = "ru") -> List[str]:
    """Подписи для OptionMenu: «48 kbps», для Edge — пометка о фиксации."""
    fixed_note = {
        "ru": " (фиксировано Edge)",
        "en": " (fixed by Edge)",
        "ja": " (Edge固定)",
        "zh": "（Edge固定）",
    }.get(lang, " (фиксировано Edge)")
    labels = []
    for kbps in supported_bitrates(backend):
        label = f"{kbps} kbps"
        if backend == "edge":
            label += fixed_note
        labels.append(label)
    return labels


def parse_bitrate_label(label: str) -> int:
    """Извлечь kbps из подписи меню."""
    try:
        return int(str(label).split()[0])
    except (ValueError, IndexError):
        return 48


def ffmpeg_lame_bitrate_args(kbps: int) -> List[str]:
    """Аргументы libmp3lame с явным CBR (без -q:a, чтобы не конфликтовать с -b:a)."""
    return ["-codec:a", "libmp3lame", "-b:a", f"{int(kbps)}k"]
