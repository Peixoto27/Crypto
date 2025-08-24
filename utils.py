# utils.py
import os
import json
from typing import Any

def save_json(path: Any, data: Any) -> bool:
    """
    Salva `data` em `path`. Tolera argumentos invertidos e garante que `path` é str.
    Cria o diretório pai se faltar.
    """
    try:
        # Se alguém chamou invertido (data, path), corrige:
        if isinstance(path, (dict, list)):
            path, data = data, path

        path = str(path)  # força ser string
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"⚠️ Erro ao salvar {path}: {e}")
        return False
