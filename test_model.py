import os, joblib, sys
import numpy, sklearn, joblib as jl
try:
    import lightgbm
except Exception as e:
    lightgbm = None
    print("LightGBM import error:", e)

path = os.getenv("MODEL_FILE", "model/model.pkl")
print("CWD =", os.getcwd())
print("MODEL_FILE =", path)
print("Exists? =", os.path.exists(path))
print("Versions -> numpy", numpy.__version__, "sklearn", sklearn.__version__, "joblib", jl.__version__, "lightgbm", getattr(lightgbm, "__version__", "None"))

try:
    m = joblib.load(path)
    print("✅ Carregado com sucesso:", type(m))
except Exception as e:
    print("❌ Falhou ao carregar:", repr(e))
