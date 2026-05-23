"""
models/ — ML signal models (LSTM, TFT, XGBoost/LightGBM, RL).

Implementations of ``SignalModel``. Every model exposes a complete
``ComponentManifest``. Device portability is required: a model trained on
CUDA must load and infer on MPS or CPU without source changes (state_dict
+ ``map_location``).

Promoted artifacts live under ``models/promoted/``; candidate artifacts
under ``experiments/candidates/``. ``model_registry`` enforces that the
production strategy only loads promoted artifacts.
"""
