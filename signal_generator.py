# signal_generator.py
from typing import Dict, Any
from signal_model import normalize_signal  # ✅ Ajustado para singular
import json
import os

SIGNALS_FILE = "signals.json"

def append_signal(signal: Dict[str, Any]):
    """
    Adiciona um novo sinal ao arquivo signals.json.
    """
    # Normaliza o sinal antes de salvar
    signal = normalize_signal(signal)

    # Carrega sinais existentes
    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE, "r") as f:
            try:
                signals = json.load(f)
            except json.JSONDecodeError:
                signals = []
    else:
        signals = []

    # Adiciona novo sinal
    signals.append(signal)

    # Salva de volta
    with open(SIGNALS_FILE, "w") as f:
        json.dump(signals, f, indent=4)

    print(f"✅ Sinal adicionado: {signal['symbol']} | Confiança: {signal['confidence']:.2f}")

def normalize_and_save(signals_list):
    """
    Normaliza todos os sinais e salva no arquivo.
    """
    normalized = [normalize_signal(s) for s in signals_list]
    with open(SIGNALS_FILE, "w") as f:
        json.dump(normalized, f, indent=4)
    print(f"💾 {len(normalized)} sinais salvos.")
