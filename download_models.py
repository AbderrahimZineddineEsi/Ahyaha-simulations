"""
Downloads the model weights from Hugging Face into m1/models, m2/models, m3/models, m4/models.

    python download_models.py          # weights only
    python download_models.py --data   # weights + the M3 and M4 training tables

The repo is private for now: run `hf auth login` first, or set HF_TOKEN.
"""
import argparse

from huggingface_hub import snapshot_download

REPO = "ZenoAbderrahim/solvex-crash-models"

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--data", action="store_true", help="also download the M3 and M4 training tables")
args = ap.parse_args()

patterns = ["m*/models/*"] + (["m*/data/*"] if args.data else [])
snapshot_download(REPO, allow_patterns=patterns, local_dir=".")
print("Done: weights are in m1/models, m2/models, m3/models, m4/models")
