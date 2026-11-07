"""Export the best checkpoint to a portable ONNX model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn
from torchvision import models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("anti_scam_training/export"))
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    model = models.resnet18(weights=None)
    model.fc = nn.Linear(model.fc.in_features, 2)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "visual_classifier.onnx"
    dummy = torch.zeros(1, 3, checkpoint["input_size"], checkpoint["input_size"])
    torch.onnx.export(
        model, dummy, target, input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17, dynamo=False,
    )
    config = {
        "architecture": checkpoint["architecture"],
        "labels": checkpoint["labels"],
        "input_size": checkpoint["input_size"],
        "mean": checkpoint["mean"],
        "std": checkpoint["std"],
        "input_name": "image",
        "output_name": "logits",
        "advisory_only": True,
        "model_scope": "website_screenshot_phishing_classifier",
    }
    (args.output / "model_config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Exported: {target}")


if __name__ == "__main__":
    main()
