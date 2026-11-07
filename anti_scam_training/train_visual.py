"""Train a ResNet-18 screenshot classifier with group-aware splits."""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms

from .data import load_manifest, split_records

MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


class ScreenshotDataset(Dataset):
    def __init__(self, records, training: bool = False):
        self.records = records
        self.transform = transforms.Compose([
            transforms.Resize((256, 256)),
            transforms.RandomResizedCrop(224, scale=(0.75, 1.0)) if training else transforms.CenterCrop(224),
            transforms.RandomHorizontalFlip(p=0.5) if training else transforms.Lambda(lambda image: image),
            transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.1) if training else transforms.Lambda(lambda image: image),
            transforms.ToTensor(),
            transforms.Normalize(MEAN, STD),
        ])

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        record = self.records[index]
        with Image.open(record.path) as image:
            image = image.convert("RGB")
            tensor = self.transform(image)
        return tensor, torch.tensor(record.label, dtype=torch.long)


def build_model(pretrained: bool = True):
    weights = models.ResNet18_Weights.DEFAULT if pretrained else None
    model = models.resnet18(weights=weights)
    model.fc = nn.Linear(model.fc.in_features, 2)
    return model


def set_seed(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("anti_scam_training/output"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=0.0003)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=0, help="Windows-safe default is 0")
    parser.add_argument("--no-pretrained", action="store_true")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or args.learning_rate <= 0:
        parser.error("epochs, batch-size and learning-rate must be positive")

    set_seed(args.seed)
    records = load_manifest(args.manifest)
    splits = split_records(records, seed=args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    split_path = args.output / "split_manifest.csv"
    with split_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "label", "group_id", "split"])
        for split, items in splits.items():
            writer.writerows([str(item.path), item.label, item.group, split] for item in items)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    train_loader = DataLoader(ScreenshotDataset(splits["train"], True), batch_size=args.batch_size,
                              shuffle=True, num_workers=args.workers, pin_memory=device.type == "cuda")
    val_loader = DataLoader(ScreenshotDataset(splits["validation"]), batch_size=args.batch_size,
                            shuffle=False, num_workers=args.workers, pin_memory=device.type == "cuda")

    model = build_model(pretrained=not args.no_pretrained).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    best_loss = float("inf")
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(images), labels)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * labels.size(0)
        train_loss /= len(train_loader.dataset)

        model.eval()
        val_loss, correct, count = 0.0, 0, 0
        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(device), labels.to(device)
                logits = model(images)
                val_loss += criterion(logits, labels).item() * labels.size(0)
                correct += (logits.argmax(1) == labels).sum().item()
                count += labels.size(0)
        val_loss /= count
        val_accuracy = correct / count
        history.append({"epoch": epoch, "train_loss": train_loss, "validation_loss": val_loss,
                        "validation_accuracy": val_accuracy})
        print(f"Epoch {epoch}/{args.epochs} train_loss={train_loss:.4f} "
              f"val_loss={val_loss:.4f} val_accuracy={val_accuracy:.4f}")
        if val_loss < best_loss:
            best_loss = val_loss
            torch.save({
                "model_state": model.cpu().state_dict(),
                "architecture": "resnet18",
                "labels": ["legitimate", "phishing"],
                "mean": list(MEAN), "std": list(STD), "input_size": 224,
                "seed": args.seed, "best_validation_loss": best_loss,
            }, args.output / "best.pt")
            model.to(device)

    (args.output / "training_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"Best checkpoint: {args.output / 'best.pt'}")


if __name__ == "__main__":
    main()
