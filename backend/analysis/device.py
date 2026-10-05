"""GPU oder CPU? Einmal prüfen und laut sagen, was genommen wird."""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def torch_device(preference: str = "auto") -> str:
    """"cuda", wenn gewünscht und torch.cuda.is_available(), sonst "cpu". Wird nie stillschweigend angenommen."""
    import torch

    wanted = preference.lower()
    if wanted == "cpu":
        log.info("Rechne auf der CPU (so eingestellt)")
        return "cpu"
    if torch.cuda.is_available():
        log.info("CUDA verfügbar: %s (torch %s), rechne auf der GPU", torch.cuda.get_device_name(0), torch.__version__)
        return "cuda"
    hint = "torch ohne CUDA installiert? Siehe README, Abschnitt Phase 4." if "+cpu" in torch.__version__ or \
        torch.version.cuda is None else "Grafiktreiber aktuell?"
    if wanted == "cuda":
        log.warning("device: cuda eingestellt, aber torch.cuda.is_available() ist False (%s) Nehme die CPU.", hint)
    else:
        log.info("Keine CUDA-GPU gefunden (torch.cuda.is_available() = False, %s) Rechne auf der CPU.", hint)
    return "cpu"
