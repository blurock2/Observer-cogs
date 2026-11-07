import csv

import pytest
from PIL import Image

from anti_scam_training.data import ImageRecord, load_manifest, parse_label, split_records


def test_parse_label_aliases():
    assert parse_label("phishing") == 1
    assert parse_label("1") == 1
    assert parse_label("legitimate") == 0
    assert parse_label("0") == 0
    with pytest.raises(ValueError):
        parse_label("maybe")


def test_manifest_loads_relative_paths_and_labels(tmp_path):
    rows = []
    for label, prefix, color in (("legitimate", "legit", 0), ("phishing", "phish", 180)):
        for index in range(3):
            name = f"{prefix}-{index}.png"
            Image.new("RGB", (16, 16), (color, index, 0)).save(tmp_path / name)
            rows.append((name, label, f"{prefix}-site-{index}"))
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["path", "label", "group_id"])
        writer.writerows(rows)
    loaded = load_manifest(manifest)
    assert len(loaded) == 6
    assert [record.label for record in loaded].count(0) == 3
    assert [record.label for record in loaded].count(1) == 3


def test_split_keeps_groups_disjoint_and_all_classes_present():
    records = []
    for label in (0, 1):
        for group in range(10):
            records.append(ImageRecord(path=None, label=label, group=f"{label}-{group}"))
    splits = split_records(records, seed=17)
    group_sets = [{record.group for record in items} for items in splits.values()]
    assert group_sets[0].isdisjoint(group_sets[1])
    assert group_sets[0].isdisjoint(group_sets[2])
    assert group_sets[1].isdisjoint(group_sets[2])
    assert all({record.label for record in items} == {0, 1} for items in splits.values())


def test_identical_images_cannot_cross_splits():
    records = []
    for label in (0, 1):
        for group in range(5):
            records.append(ImageRecord(path=None, label=label, group=f"{label}-{group}",
                                       fingerprint=f"hash-{label}-{group}"))
    # Same exact file hash, even with different declared group IDs, is merged.
    records.append(ImageRecord(path=None, label=0, group="different-site", fingerprint="hash-0-0"))
    splits = split_records(records, seed=3)
    owner = {}
    for split, items in splits.items():
        for item in items:
            if item.fingerprint:
                assert item.fingerprint not in owner or owner[item.fingerprint] == split
                owner[item.fingerprint] = split


def test_split_rejects_too_few_independent_groups():
    records = [ImageRecord(path=None, label=label, group=f"{label}-{i}")
               for label in (0, 1) for i in range(2)]
    with pytest.raises(ValueError):
        split_records(records)
