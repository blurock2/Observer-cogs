"""Evaluate a saved checkpoint on the held-out test groups."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
from torch.utils.data import DataLoader
from torchvision import models, transforms
from torch import nn

from .data import load_manifest, split_records
from .train_visual import ScreenshotDataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    records = load_manifest(args.manifest)
    test_records = split_records(records, seed=args.seed)["test"]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = models.resnet18(weights=None)
    model.fc = nn.Linear(model.fc.in_features, 2)
    model.load_state_dict(checkpoint["model_state"])
    model.to(device).eval()

    loader = DataLoader(ScreenshotDataset(test_records), batch_size=args.batch_size, shuffle=False)
    truth, predictions = [], []
    with torch.no_grad():
        for images, labels in loader:
            logits = model(images.to(device))
            predictions.extend(logits.argmax(1).cpu().tolist())
            truth.extend(labels.tolist())

    matrix = confusion_matrix(truth, predictions, labels=[0, 1])
    precision, recall, f1, _ = precision_recall_fscore_support(
        truth, predictions, labels=[1], average=None, zero_division=0
    )
    tn, fp, fn, tp = matrix.ravel()
    result = {
        "device": str(device),
        "test_samples": len(truth),
        "confusion_matrix_labels": ["true_legitimate", "true_phishing"],
        "confusion_matrix": matrix.tolist(),
        "phishing_precision": float(precision[0]),
        "phishing_recall": float(recall[0]),
        "phishing_f1": float(f1[0]),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else 0.0,
        "warning": "Website screenshot metrics do not establish safety on Discord/game screenshots.",
    }
    rendered = json.dumps(result, indent=2)
    print(rendered)
    output = args.output or args.checkpoint.parent / "evaluation.json"
    output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
