# Third-party notices

This repository is licensed under the MIT license (`LICENSE`). The container
images built from it install, and at runtime download, third-party components
that the MIT license does not cover and that carry terms of their own. This
file records the ones a redistributor has to act on.

It is not a complete bill of materials. The exact, version-pinned dependency
set of each service lives in that service's own lock file, and each entry below
names the file and line it was read from.

## What this means in practice

Whoever builds these container images and passes them on redistributes the
Apache-2.0 components listed below and takes on the Apache-2.0 obligations for
them: ship the license text, keep the existing copyright, patent, trademark and
attribution notices, include the NOTICE file where the component has one, and
mark files that were changed. The model weights are not baked into any image —
they are downloaded on first start into a mounted cache (`README.md:184`,
`README.md:185`) — so whoever operates the stack, or ships an image with a
pre-warmed cache, accepts each model's own terms as set by its provider; the
MIT license of this repository does not extend to them.

## OCR stack — Weave Ingest worker image

`services/ingest/backend/worker.Dockerfile` builds the worker image the compose
stack refers to as `ghcr.io/bl0rb/weave-ingest-worker`
(`deploy/docker-compose.weave.yml:308`, built from that Dockerfile in
`deploy/docker-compose.local.yml:17` to `:21`). It installs PaddlePaddle
directly and the rest from two hash-pinned locks,
`services/ingest/backend/requirements.txt` (`worker.Dockerfile:27`) and
`services/ingest/backend/requirements-worker.txt` (`:48`), on top of the Debian
packages added in `:12` to `:22`.

| Component | Version | Pinned in | License |
|---|---|---|---|
| `paddleocr[doc-parser]` | 3.7.0 | `requirements-worker.txt:1385`, requested by `requirements-worker.in:15` | Apache-2.0 |
| `paddlex[genai-client,ocr,ocr-core]` | 3.7.2 | `requirements-worker.txt:1389`, pulled in by `paddleocr` (`:1392`) | Apache-2.0 |
| `paddlepaddle` | 3.2.1 | `worker.Dockerfile:36` (arm64 path) | Apache-2.0 |
| `paddlepaddle-gpu` | 3.2.1 | `worker.Dockerfile:33`, from `https://www.paddlepaddle.org.cn/packages/stable/cu126/` (`:34`, amd64 path) | Same upstream project as `paddlepaddle`. This wheel is served from the vendor's own index rather than PyPI and is a CUDA 12.6 build; the terms of the CUDA components it carries have to be checked with the vendor. |
| `opencv-contrib-python` | 4.10.0.84 | `requirements-worker.txt:1364`, pulled in by `paddlex` (`:1372`) | Apache-2.0 |

PaddleOCR, PaddleX and PaddlePaddle are published by Baidu. The lock file pulls
a large transitive tree beyond the rows above; every entry in it carries its own
terms, and the file is the authoritative list of what the image contains.

## Reranker

`services/reranker/Dockerfile` builds `ghcr.io/bl0rb/weave-reranker`
(`deploy/docker-compose.weave.yml:543`, `:634`). `torch` is installed first
from the CPU wheel index, not from PyPI (`Dockerfile:26`,
`requirements.in:22`).

| Component | Version | Pinned in | License |
|---|---|---|---|
| `torch` | 2.13.0 | `requirements.txt:64`, installed from `https://download.pytorch.org/whl/cpu` (`Dockerfile:26`) | Declared by the wheel as `Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT`, with its own bundled third-party license files |
| `sentence-transformers` | 6.0.1 | `requirements.txt:57`, `requirements.in:30` | Apache-2.0, ships a NOTICE file |
| `transformers` | 5.16.1 | `requirements.txt:66`, pulled in by `sentence-transformers` | Apache-2.0 |
| `tokenizers` | 0.23.1 | `requirements.txt:63` | Apache-2.0 |
| `huggingface_hub` | 1.29.0 | `requirements.txt:31` | Apache-2.0 |

## Embeddings

`services/embeddings/Dockerfile` builds `ghcr.io/bl0rb/weave-embeddings`
(`deploy/docker-compose.weave.yml:542`, `:571`). This service deliberately
runs the model's ONNX graph directly instead of through a higher-level framework
(`services/embeddings/requirements.in:13` to `:20`).

| Component | Version | Pinned in | License |
|---|---|---|---|
| `onnxruntime` | 1.29.0 | `requirements.in:36`, `requirements.txt:29` | MIT |
| `tokenizers` | 0.23.1 | `requirements.in:37`, `requirements.txt:41` | Apache-2.0 |
| `huggingface_hub` | 1.29.0 | `requirements.in:38`, `requirements.txt:25` | Apache-2.0 |

## Model weights loaded at runtime

None of these weights are part of this repository or of any image built from it.
They are fetched on first start into a cache that a deployment mounts as a
volume, and they are governed by their providers, not by this repository.

| Model | Loaded by | Fetched from | License |
|---|---|---|---|
| `intfloat/multilingual-e5-small` (`onnx/model.onnx`, `onnx/tokenizer.json`) | `services/embeddings/app/core/config.py:28`, downloaded in `app/services/encoder.py:293` and `:296` | Hugging Face Hub (`README.md:184`) | Not established anywhere in this repository. Check the model card of the provider before redistributing or operating it. |
| `BAAI/bge-reranker-v2-m3` | `services/reranker/app/core/config.py:27`, loaded through `sentence-transformers`' `CrossEncoder` in `app/services/model.py:68` | Hugging Face Hub (`README.md:184`) | Not established anywhere in this repository. Check the model card of the provider. |
| `PP-OCRv6_tiny_det` / `_rec`, `PP-OCRv6_small_det` / `_rec`, `PP-OCRv6_medium_det` / `_rec`, and the PP-StructureV3 pipeline | `services/ingest/backend/app/services/paddle_service.py:53` to `:107` | PaddleX default model source, `*.bcebos.com`, or Hugging Face with `PADDLE_PDX_MODEL_SOURCE=HuggingFace` (`README.md:185`, `services/ingest/charts/weave-ingest/values.yaml:187`) | Not established anywhere in this repository. Check the terms of the PaddleOCR / PaddleX model source. |
| `PaddleOCR-VL-1.6-0.9B` | `services/ingest/backend/app/services/paddle_service.py:108` to `:116` | Same sources as the PP-OCRv6 weights above | Not established anywhere in this repository. Check the terms of the PaddleOCR / PaddleX model source. |

The download targets are `$HOME/.paddlex` and `$HOME/.paddleocr` inside the
worker container (`worker.Dockerfile:63`, `values.yaml:148`); the embeddings and
reranker services cache their downloads under `EMBEDDINGS_CACHE_DIR` and
`RERANKER_CACHE_DIR` (`services/embeddings/Dockerfile:29`,
`services/reranker/Dockerfile:12`).

## Base images

| Image | Used by | Terms |
|---|---|---|
| `pgvector/pgvector:pg16` | `deploy/docker-compose.weave.yml:174` | Published by the pgvector project; carries PostgreSQL 16 and the pgvector extension with their own licenses. Check the image and its sources. |
| `redis:7` | `deploy/docker-compose.weave.yml:214` | `7` is a floating tag, and Redis licensing differs across 7.x releases. Determine the terms for the exact image actually pulled. |
| `python:3.13-slim`, `python:3.12-slim` | every Python service Dockerfile, for example `services/ingest/backend/worker.Dockerfile:1`, `services/reranker/Dockerfile:1` | Official Python images on a Debian base; the distribution packages they contain carry their own licenses. |
| `node:26-alpine` | `services/chat/Dockerfile:1`, `services/ingest/frontend/Dockerfile:1` | Official Node.js images on an Alpine base; the distribution packages they contain carry their own licenses. The npm dependency trees of both frontends are pinned in the respective `package-lock.json` and carry their own terms. |

## Origin and naming

Weave Ingest is based on the project PaddleDoc
(`services/ingest/README.md:19`); its changelog history up to 08/2026
originates there and still refers to the product under that former name
(`services/ingest/CHANGELOG.md:8` to `:10`). Traces of that origin remain in
identifiers across the repository, for example the `X-PaddleDoc-Signature`
webhook header (`services/ingest/CHANGELOG.md:78`).

The name "PaddleDoc", and the `Paddle` prefix wherever it appears here, point at
the third-party project PaddlePaddle/PaddleOCR, whose OCR stack Weave Ingest
builds on (`README.md:28`, `services/ingest/README.md:15`). Neither PaddleDoc
nor Weave is affiliated with, endorsed by, or sponsored by that project or by
Baidu. The names appear only to identify the upstream software.

## How the license column was determined

Each license above is the one declared by the package metadata of the exact
version pinned in this repository — the `License`, `License-Expression` or
license classifier field of that distribution. That metadata is not itself part
of this repository; only the version pins are, and each row names the file and
line they were read from. Where this repository establishes no license at all,
which is the case for every model weight, the table says so instead of naming
one.
