# Environment check for local models (SEM0)

Measured by `python scripts/check_env.py` on 2026-10-03 14:13. Read-only and offline; re-run it to refresh this file.

## Requirements

| Requirement | Measured | Needed | Verdict |
|---|---|---|---|
| RAM total | 7.7 GB | >= 7.5 GB | PASS |
| Cache drive free | 67.2 GB on F: | >= 8 GB | PASS |
| Repo drive free | 8.2 GB on C: | >= 2 GB | PASS |
| GPU | none (torch is not installed, so CUDA was not checked) | not required (the models run on the CPU) | PASS |

Overall: PASS. A GPU is not required.

Free RAM is not a requirement here. It is a runtime guard in the embedding script, which needs about 1 GB free when it starts.

Decision (2026-10-03): No PyTorch. The embedding backend is FastEmbed on ONNX Runtime, default model BAAI/bge-small-en-v1.5 (384-dim, quantised). Reason: the repo drive has about 4 GB free and a torch install would take most of it. Nothing is installed by this check.

Cache placement: keep (F:\huggingface). F: has 67.2 GB free, at least 8 GB.

## Machine

| Item | Value |
|---|---|
| CPU cores | 8 |
| RAM total | 7.7 GB |
| RAM free | 2.1 GB |
| GPU (CUDA seen by torch) | none (torch is not installed, so CUDA was not checked) |
| Python | 3.11.16 |
| Conda env | job-agent |
| Hugging Face cache | F:\huggingface (4.3 GB) |

## Drives

| Drive | Type | Bus | Total | Free |
|---|---|---|---|---|
| C: | fixed | RAID | 132.5 GB | 8.2 GB |
| D: | fixed | RAID | 97.7 GB | 85.2 GB |
| E: | fixed | RAID | 97.7 GB | 56.3 GB |
| F: | fixed | RAID | 146.5 GB | 67.2 GB |

## Packages

| Package | Version |
|---|---|
| torch | not installed |
| sentence-transformers | not installed |
| huggingface_hub | 1.33.0 |

## Install footprint (measured 3 October 2026, SEM2)

Added by hand; `scripts/check_env.py` rewrites this file, so the script needs a small change before this section
survives the next run.

| What | Where | Size |
|---|---|---|
| fastembed, onnxruntime, numpy, rank-bm25 and their requirements | C:, the `job-agent` env's site-packages (338 MB before, 488 MB after) | 150 MB |
| Model `Qdrant/bge-small-en-v1.5-onnx-Q` | F:\huggingface\fastembed | 65 MB |

Installed with `pip install --no-cache-dir`, so nothing was left in the pip cache on C:.
