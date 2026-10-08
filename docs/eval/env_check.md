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
| alembic 1.20.0 with Mako and MarkupSafe (TRK1) | C:, site-packages (488 MB before, 491 MB after) | 3 MB |

Installed with `pip install --no-cache-dir`, so nothing was left in the pip cache on C:.

## Web app footprint (measured 8 October 2026, TRK3)

Added by hand, like the section above.

| What | Where | Size |
|---|---|---|
| `web/node_modules`: next 16.4.0, react and react-dom 19.3.0, typescript 7.0.2, tailwindcss and @tailwindcss/postcss 4.3.3, postcss, vitest 5.0.3, @vitejs/plugin-react, jsdom, @testing-library/react and /dom, @playwright/test 1.64.0, @types/* | C:, inside the repository | 481 MB |
| Next's build directory (`web/.next`, and `web/.next-smoke` for the smoke run) | C:, inside the repository | 45 MB each after one run |
| npm cache | F:\npm-cache (`npm_config_cache`, set for the install command only) | 473 MB |
| Playwright browser: Chromium headless shell 1248, ffmpeg, winldd | F:\playwright-browsers (`PLAYWRIGHT_BROWSERS_PATH`) | 282 MB |

C: had 3.3 GB free before the install and 3.6 GB after it; other programs moved the figure by more than the install
did during the hour, so the sizes above are measured per folder. Nothing was installed globally and no npm or
Playwright setting was changed outside the commands that were run. Set `PLAYWRIGHT_BROWSERS_PATH=F:\playwright-browsers`
before `npm run smoke`; without it Playwright looks on C: and finds no browser.
