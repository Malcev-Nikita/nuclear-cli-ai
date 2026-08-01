"""Распознавание речи: faster-whisper (+ CUDA-грабли Windows)."""

from __future__ import annotations

import os
import sys

from config import SAMPLE_RATE, WHISPER_BEAM, WHISPER_DEVICE, WHISPER_MODEL


def _add_cuda_dll_dirs() -> None:
    """ctranslate2 на Windows не находит cublas/cudnn сам — добавляем bin-папки
    pip-пакетов nvidia-* в поиск DLL."""
    if sys.platform != "win32":
        return
    import site
    from pathlib import Path

    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        for bin_dir in Path(root).glob("nvidia/*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel

        if WHISPER_DEVICE in ("auto", "cuda"):
            _add_cuda_dll_dirs()
            try:
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
                self._warmup(model)  # CUDA-DLL отваливаются только на первом encode
                self.model, self.device = model, "cuda"
                return
            except Exception as error:
                if WHISPER_DEVICE == "cuda":
                    raise
                reason = str(error).splitlines()[0]
                print(f"   (cuda не завёлся: {reason} — работаю на cpu; для gpu:")
                print("    python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12)")
        self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        self._warmup(self.model)
        self.device = "cpu"

    @staticmethod
    def _warmup(model) -> None:
        import numpy as np

        segments, _ = model.transcribe(np.zeros(SAMPLE_RATE // 2, dtype="float32"),
                                       language="ru", beam_size=1)
        list(segments)

    def transcribe(self, audio) -> str:
        # Без initial_prompt: whisper «эхом» дописывал подсказку на шуме и сам
        # себе командовал. Вместо неё — фильтр галлюцинаций по уверенности.
        segments, _ = self.model.transcribe(
            audio,
            language="ru",
            beam_size=WHISPER_BEAM,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(
            s.text.strip() for s in segments
            if s.no_speech_prob < 0.7 and s.avg_logprob > -1.2
        ).strip()
