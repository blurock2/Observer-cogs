# Observer visual anti-scam training

This package trains an **offline visual phishing-page classifier** on Windows/GPU and exports a separate ONNX model for later VPS deployment. It does not change the running Discord bot or enable moderation.

## Dataset layout

Keep downloaded datasets outside Git. Convert the two source datasets into one CSV manifest with columns:

- `path`: image path, absolute or relative to the manifest
- `label`: `phishing` / `malicious` / `1`, or `legitimate` / `benign` / `0`
- `group_id` (optional): original page/domain/sample identifier; images from the same website should share a group

Example:

```csv
path,label,group_id
phishing/0001.png,phishing,example-phish-domain
legitimate/0001.png,legitimate,example-bank-domain
```

Do not infer labels from directory names or filenames. Use the labels supplied by the dataset. Prefer grouping by registrable domain or source sample ID; the pipeline also groups byte-identical images by SHA-256 to prevent exact duplicates crossing splits.

The Phishing and Legitimate Websites V2 and Phish360 releases may use different archive layouts and schemas. Review their licences and create a manifest for each, then combine the rows. Do not redistribute their images or derived data unless their licences permit it.

## Windows setup

Use the activated `.venv-training` environment. Install the training-only dependencies:

```powershell
python -m pip install torch torchvision onnx onnxruntime pillow pandas scikit-learn matplotlib tqdm
```

## Train

```powershell
python -m anti_scam_training.train_visual --manifest anti_scam_training/datasets/manifest.csv --output anti_scam_training/output --epochs 12 --batch-size 16
```

Use `--no-pretrained` to avoid downloading ImageNet weights. On an 8 GB RTX 4060 Ti, start with batch size 8 or 16. Training automatically selects CUDA when available.

The script creates a group-aware train/validation/test split, checks that both classes are represented, and writes the best validation checkpoint and split manifest to the output directory.

## Evaluate and export

```powershell
python -m anti_scam_training.evaluate --checkpoint anti_scam_training/output/best.pt --manifest anti_scam_training/datasets/manifest.csv
python -m anti_scam_training.export_model --checkpoint anti_scam_training/output/best.pt --output anti_scam_training/export
```

Evaluation reports confusion matrix, precision, recall, F1, and false-positive rate. Inspect false positives manually, especially ordinary game screenshots and Discord content. These website datasets do not represent Discord scam images or general game screenshots; add a separately labelled Discord-specific holdout set before considering deployment.

Export produces `visual_classifier.onnx` and `model_config.json`. Keep model files separate from source control and transfer them to the VPS only after review.

## Safety and limitations

- This is a website screenshot classifier, not OCR training and not a general-purpose scam detector.
- A visual score is advisory evidence only. It must not independently cause deletion, timeout, permanent mute, or ban.
- Keep the bot in detection/review-only mode until representative Discord-specific testing demonstrates an acceptable false-positive rate.
- The exported model is not wired into `cogs/anti_scam.py` by this package.
- Dataset licences, attribution, and redistribution terms remain the operator's responsibility.
