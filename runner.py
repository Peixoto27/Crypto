# -*- coding: utf-8 -*-
# runner.py — Executa main.run_pipeline() em loop no intervalo configurável

import os
import time
import traceback

import main  # usa seu main.py existente, que já tem run_pipeline()

def _sleep_smart(total_seconds: int):
    step = 30
    left = max(0, int(total_seconds))
    while left > 0:
        s = min(step, left)
        print(f"⏳ aguardando {s}s… (restante {left}s)")
        time.sleep(s)
        left -= s

if __name__ == "__main__":
    interval_min = float(os.getenv("RUN_INTERVAL_MIN", "15"))  # ajuste no Railway
    min_pause_sec = 60  # garante pelo menos 60s entre ciclos

    print(f"▶️ Runner iniciado. Intervalo = {interval_min} min.")
    while True:
        started = time.time()
        try:
            # chama a sua pipeline original
            main.run_pipeline()
        except Exception as e:
            print("❌ Erro inesperado no ciclo:", e)
            traceback.print_exc()

        elapsed = time.time() - started
        wait_sec = max(min_pause_sec, int(interval_min * 60 - elapsed))
        print(f"✅ Ciclo concluído em {int(elapsed)}s. Próxima execução em ~{wait_sec}s.")
        _sleep_smart(wait_sec)
