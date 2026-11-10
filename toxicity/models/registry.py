"""Registre des modèles disponibles (BERT, LSTM, LAYA).

Les imports sont paresseux : seul le module du modèle demandé est chargé.
"""

from importlib import import_module

from toxicity.models.base import ToxicityModel

# nom -> "module:Classe"
MODELS: dict[str, str] = {
    "bert": "toxicity.models.bert:BertToxicityModel",
    "lstm": "toxicity.models.lstm:LstmToxicityModel",
    "laya": "toxicity.models.laya:LayaToxicityModel",
}


def get_model_class(name: str) -> type[ToxicityModel]:
    if name not in MODELS:
        raise ValueError(f"Modèle inconnu '{name}'. Disponibles : {', '.join(MODELS)}")
    module_path, class_name = MODELS[name].split(":")
    return getattr(import_module(module_path), class_name)
