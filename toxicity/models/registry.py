"""Registre des modèles disponibles : ajouter ici LSTM et LAYA quand ils existeront.

Les imports sont paresseux pour ne pas charger transformers quand on utilise
un modèle qui n'en a pas besoin (ex. LAYA / scikit-learn).
"""

from importlib import import_module

from toxicity.models.base import ToxicityModel

# nom -> "module:Classe"
MODELS: dict[str, str] = {
    "bert": "toxicity.models.bert:BertToxicityModel",
    # "lstm": "toxicity.models.lstm:LstmToxicityModel",
    # "laya": "toxicity.models.laya:LayaToxicityModel",
}


def get_model_class(name: str) -> type[ToxicityModel]:
    if name not in MODELS:
        raise ValueError(f"Modèle inconnu '{name}'. Disponibles : {', '.join(MODELS)}")
    module_path, class_name = MODELS[name].split(":")
    return getattr(import_module(module_path), class_name)
