import os
import sys

# App-Package importierbar machen (Tests laufen aus backend/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Minimale Env, damit get_settings() ohne .env funktioniert
os.environ.setdefault("SECRET_KEY", "test-secret")
