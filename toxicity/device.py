"""Sélection du matériel : CUDA (Windows / RTX 5080), MPS (Mac M3) ou CPU."""

import os
from dataclasses import dataclass

# Doit être défini avant l'import de torch : les opérations non implémentées
# sur MPS basculent sur le CPU au lieu de lever une erreur.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch  # noqa: E402


@dataclass(frozen=True)
class DeviceInfo:
    device: torch.device
    amp_dtype: torch.dtype | None  # None = pas de précision mixte
    pin_memory: bool
    default_batch_size: int

    @property
    def type(self) -> str:
        return self.device.type

    def autocast(self):
        """Contexte de précision mixte (no-op si non supporté)."""
        if self.amp_dtype is None:
            return torch.autocast(device_type=self.type, enabled=False)
        return torch.autocast(device_type=self.type, dtype=self.amp_dtype)


def get_device() -> DeviceInfo:
    if torch.cuda.is_available():
        # TF32 sur les matmuls : gain net sur Ampere+ (dont Blackwell / RTX 5080).
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        bf16 = torch.cuda.is_bf16_supported()
        return DeviceInfo(
            device=torch.device("cuda"),
            amp_dtype=torch.bfloat16 if bf16 else torch.float16,
            pin_memory=True,
            default_batch_size=32,
        )
    if torch.backends.mps.is_available():
        # fp32 sur MPS : l'autocast y reste moins fiable.
        return DeviceInfo(torch.device("mps"), None, False, 16)
    return DeviceInfo(torch.device("cpu"), None, False, 16)


def describe(info: DeviceInfo) -> str:
    if info.type == "cuda":
        name = torch.cuda.get_device_name(0)
        return f"cuda ({name}, CUDA {torch.version.cuda}, amp={info.amp_dtype})"
    return f"{info.type} (amp=off)"
