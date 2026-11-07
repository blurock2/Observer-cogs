"""Dataset manifest parsing, validation, hashing, and leakage-safe splitting."""
from __future__ import annotations

import csv
import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

POSITIVE = {"1", "phishing", "malicious", "scam", "positive"}
NEGATIVE = {"0", "legitimate", "benign", "safe", "negative"}


@dataclass(frozen=True)
class ImageRecord:
    path: Path
    label: int
    group: str


def parse_label(value: str) -> int:
    normalized = str(value).strip().lower()
    if normalized in POSITIVE:
        return 1
    if normalized in NEGATIVE:
        return 0
    raise ValueError(f"Unsupported label {value!r}; use phishing/1 or legitimate/0")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest: str | Path, verify_images: bool = True) -> list[ImageRecord]:
    manifest = Path(manifest).expanduser().resolve()
    records: list[ImageRecord] = []
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "label" not in reader.fieldnames:
            raise ValueError("Manifest must contain a 'label' column")
        path_column = "path" if "path" in reader.fieldnames else "image_path"
        if path_column not in reader.fieldnames:
            raise ValueError("Manifest must contain a 'path' or 'image_path' column")
        for line, row in enumerate(reader, start=2):
            raw_path = (row.get(path_column) or "").strip()
            if not raw_path:
                raise ValueError(f"Missing image path on CSV line {line}")
            image_path = Path(raw_path).expanduser()
            if not image_path.is_absolute():
                image_path = (manifest.parent / image_path).resolve()
            if not image_path.is_file():
                raise FileNotFoundError(f"Image on CSV line {line} does not exist: {image_path}")
            label = parse_label(row.get("label", ""))
            if verify_images:
                try:
                    with Image.open(image_path) as image:
                        image.verify()
                except (UnidentifiedImageError, OSError) as exc:
                    raise ValueError(f"Invalid image on CSV line {line}: {image_path}") from exc
            supplied_group = (row.get("group_id") or "").strip()
            # Hash grouping prevents identical files from leaking between splits.
            group = supplied_group or sha256_file(image_path)
            records.append(ImageRecord(image_path, label, group))
    if len(records) < 6:
        raise ValueError("At least 6 labelled images are required for train/validation/test splits")
    if {record.label for record in records} != {0, 1}:
        raise ValueError("Manifest must contain both legitimate (0) and phishing (1) images")
    return records


def split_records(records: list[ImageRecord], seed: int = 42,
                  test_fraction: float = 0.15, validation_fraction: float = 0.15):
    """Deterministic group split; never places one group in multiple partitions."""
    if not 0 < test_fraction < 0.5 or not 0 < validation_fraction < 0.5:
        raise ValueError("Split fractions must be between 0 and 0.5")
    groups: dict[str, list[ImageRecord]] = {}
    for record in records:
        groups.setdefault(record.group, []).append(record)
    if len(groups) < 3:
        raise ValueError("At least 3 distinct groups are required for leakage-safe splitting")

    # Shuffle groups within each class and allocate groups independently.
    by_label: dict[int, list[str]] = {0: [], 1: []}
    for group, items in groups.items():
        labels = {item.label for item in items}
        if len(labels) != 1:
            raise ValueError(f"Group {group!r} contains conflicting labels")
        by_label[next(iter(labels))].append(group)

    rng = random.Random(seed)
    partitions = {"train": [], "validation": [], "test": []}
    for label, label_groups in by_label.items():
        rng.shuffle(label_groups)
        if len(label_groups) < 3:
            raise ValueError(
                f"Class {label} needs at least 3 distinct groups; found {len(label_groups)}"
            )
        test_n = max(1, round(len(label_groups) * test_fraction))
        val_n = max(1, round(len(label_groups) * validation_fraction))
        if test_n + val_n >= len(label_groups):
            test_n = val_n = 1
        test_groups = label_groups[:test_n]
        val_groups = label_groups[test_n:test_n + val_n]
        train_groups = label_groups[test_n + val_n:]
        for name, selected in (("train", train_groups), ("validation", val_groups), ("test", test_groups)):
            partitions[name].extend(item for group in selected for item in groups[group])

    for name, items in partitions.items():
        if not items or {item.label for item in items} != {0, 1}:
            raise ValueError(f"{name} split does not contain both classes; add more independent groups")
    return partitions
