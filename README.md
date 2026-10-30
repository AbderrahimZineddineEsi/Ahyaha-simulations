# SolveX 3.0 · Crash response

A citizen app and an in-car **blackbox** detect road crashes in Algeria, estimate how badly people are hurt, and send that picture to the Protection Civile, the police or Gendarmerie, and the hospital. The system keeps working when the network is weak or gone.

- **[PROJECT.md](PROJECT.md):** the concept, the Algerian context, what we're building, blackbox sensors, suggestions
- **[MODEL_SPEC.md](MODEL_SPEC.md):** each model's inputs, outputs, data, examples, scores and speed
- **[Model weights on Hugging Face](https://huggingface.co/ZenoAbderrahim/solvex-crash-models)**

## The four models

| | Model | Output | Test score | Status |
|---|---|---|---|---|
| **M1** | Road risk | low, medium, high, very high | AUC 0.800 | trained, one flaw to fix |
| **M2** | Crash detection | none, possible, likely, crash | AP 0.990 | blackbox trained, phone side not started |
| **M3** | Injury severity | low, medium, high, extreme | AUC 0.889 | trained |
| **M4** | Injury map | unlikely, check, likely, per body region | mean AUC 0.827 | trained |

One crash goes from raw signals through M2, M3 and M4 in about 8 ms on one laptop CPU core.

**Trained doesn't mean working in the app yet.** Still to do:
- M1: fix the missing-data flaw, and build the OpenStreetMap and weather lookups
- `crash_summary()`: fix ΔV underestimates and the `multi_impact` threshold
- Phone-side crash detection, and the confidence ladder that combines all signals
- A backend AI API serving the four models (M2 is live over WebSocket in `server/`; M1, M3 and M4 aren't yet)
- The profile rule (pregnancy, heart disease, blood thinners raise M3 by one level)

## Getting started

```bash
pip install -r requirements.txt
python download_models.py          # weights into m1/models ... m4/models
python download_models.py --data   # also the M3 and M4 training tables
```

The Hugging Face repo is private for now, so you need access to it: run `hf auth login` first, or set `HF_TOKEN`.

[MODEL_SPEC.md](MODEL_SPEC.md#using-the-models) has code that runs all four models.

## Running the server

```bash
python server/app.py
python server/example_client.py crash    # in a second terminal, to test it
```

Open `http://localhost:8000/` to watch the status of each car live.

A simulated blackbox streams one second of telemetry at a time over WebSocket. The server saves every second to `server/solvex.db` and checks it for a trigger (1 g on x-y). When one fires, M2 scores the 8 s before and the 8 s after it. The server prints the address to give to the other PC. [server/PROTOCOL.md](server/PROTOCOL.md) documents the messages and the scenarios.

## Repository layout

```
README.md             start here
PROJECT.md            the full project plan
MODEL_SPEC.md         what each model does and how to use it
download_models.py    gets the weights from Hugging Face
m1/  road risk        train_m1.ipynb (cleaned-up version, not run yet)
                      m1_kaggle_run.ipynb (the run that produced the saved model)
m2/  crash detection  m2_blackbox.py (signal code, shared with the backend)
                      train_m2_kaggle.ipynb (import into Kaggle)
                      train_m2_kaggle_finished.ipynb (the same notebook with its outputs)
                      build_m2_notebook.py (regenerates the notebook)
m3/  severity         train_m3.ipynb, build_m3_dataset.py
m4/  injury map       train_m4.ipynb, build_m4_dataset.py
server/               WebSocket server: app.py, stream.py (trigger and windows), database.py, m2_model.py,
                      monitor.html (the live page), PROTOCOL.md (the messages),
                      example_client.py, examples/ (ready-to-send messages)
archive/              early notes and the first version of the plan
```

Weights (`m*/models/`) and data (`m*/data/`, `m*/raw/`) aren't in git.

## Retraining

**M1 (needs the Kaggle datasets):** [training table with weather](https://www.kaggle.com/datasets/moussaballou/training-table-weather-csv) · [accidents 2016–2024](https://www.kaggle.com/datasets/moussaballou/accidents-2016-2024-csv). Put `training_table_weather.csv` in `m1/data/` and run `m1/train_m1.ipynb`.

**M3 and M4 (CPU):** `python download_models.py --data`, then run the notebooks. To rebuild the tables from the raw NHTSA files, use `build_m3_dataset.py` and `build_m4_dataset.py`.

**M2 (Kaggle GPU):**
1. Accept the [VZCrash](https://huggingface.co/datasets/vzc-research-chapter/VZCrash) terms on Hugging Face and create a read token.
2. Import `m2/train_m2_kaggle.ipynb` into Kaggle. Add-ons → Secrets → add `HF_TOKEN`. Settings → GPU T4 (not P100), Internet on.
3. Run up to **Preflight checks**, then Save Version → Save & Run All.
4. Download `m2_models.zip` and unzip it into `m2/models/`.

If you edit `m2/m2_blackbox.py`, run `python m2/build_m2_notebook.py` to put the change into the notebook.

## Data and licenses

- **M1:** French BAAC road accident data (ONISR, data.gouv.fr)
- **M2:** [VZCrash](https://arxiv.org/abs/2606.06074), CC BY-NC 4.0, so the M2 weights are non-commercial
- **M3, M4:** NHTSA [CISS](https://www.nhtsa.gov/crash-data-systems/crash-investigation-sampling-system) and [NASS](https://www.nhtsa.gov/crash-data-systems/national-automotive-sampling-system)
- **M3 paper reference:** [Serious injury prediction with trainable B-splines (2026)](https://pubmed.ncbi.nlm.nih.gov/42102299/)
