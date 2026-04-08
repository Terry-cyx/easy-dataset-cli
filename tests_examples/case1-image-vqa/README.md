# Case 1 — Image VQA (汽车图片识别)

**Status:** ✅ live run completed against Easy-Dataset server with real Kimi-K2.5 vision model

Reproduces `spec/03-case-studies.md` 案例 1 — generate Visual Question
Answering (VQA) datasets for a small directory of vehicle images.

This case exercises the **two new image-aware command branches** added in the
final refine pass:

1. `questions generate --source image --question-count N --wait`
   → kicks off a server-side `image-question-generation` task
2. `datasets generate --source image --wait`
   → kicks off a server-side `image-dataset-generation` task

Both routes were previously broken in the CLI: `questions generate --source image`
hit the wrong endpoint (`/generate-questions`, which is text-only and 404s on
images), and `datasets generate` for image questions silently called the text
dataset endpoint (which can't pass the image to the model, producing
hallucinated "I cannot see images" answers). The fix routes both through the
correct background-task endpoints.

## Layout

```
input/
  make_images.py        # Pillow script that synthesizes 4 vehicle PNGs
  vehicles/             # 4 generated PNGs (red sedan, blue truck, green van, yellow taxi)
output/
  05_image_question_task.json   # ★ image-question-generation task result (status=1, completed)
  06_questions_list.json        # 6 VQA questions (3 per image × 2 unprocessed images)
  07_datasets_gen.json          # (legacy/wrong-route output, kept for diff)
  08_image_dataset_task.json    # ★ image-dataset-generation task result (6/6 completed)
  09_datasets_list.json         # confirms /datasets is empty for image rows (server-side split)
  10_image_datasets_export_raw.json  # raw /image-datasets/export response
  vqa-alpaca.json               # ★ FINAL Alpaca-style export (instruction/input/output)
```

## Pipeline (live commands)

```bash
# 0. Synthesize the test images (uses Pillow)
python tests_examples/case1-image-vqa/input/make_images.py

# 1. Project + vision model
PROJ=$(easyds --json project new --name case1-vqa-$(date +%s) | jq -r .id)
easyds --json project use $PROJ
easyds --json model set --provider-id openai \
    --provider-name Paratera \
    --endpoint https://llmapi.paratera.com/v1/ \
    --api-key sk-... \
    --model-id Kimi-K2.5 \
    --type vision \
    --temperature 0.4
# (model use is auto-set to the new vision config)

# 2. Import the image directory
easyds --json files import --type image \
    --dir tests_examples/case1-image-vqa/input/vehicles

# 3. ★ Kick off image-question-generation (3 questions per image)
easyds --json questions generate --source image \
    --question-count 3 --language 中文 \
    --wait --timeout 600
# → server task 'image-question-generation', status: 1 (completed)

# 4. ★ Kick off image-dataset-generation (vision model is given each image)
easyds --json datasets generate --source image --language 中文 \
    --wait --timeout 900
# → server task 'image-dataset-generation', completedCount: 6/6

# 5. Pull the answered datasets and convert to Alpaca
curl -sX POST http://localhost:1717/api/projects/$PROJ/image-datasets/export \
    -H 'content-type: application/json' \
    -d '{"confirmedOnly":false}' \
    > tests_examples/case1-image-vqa/output/10_image_datasets_export_raw.json
```

(Step 5 is plain curl because `easyds export run` covers text datasets only —
image datasets live in a separate `/image-datasets/export` route. Wiring this
into the CLI is a follow-up refine.)

## Result — vision model actually saw the images

Sample answers from the live run (Kimi-K2.5 vision):

| Image | Question | Answer |
|---|---|---|
| `green_van.png` | 这个场景的主要颜色是什么？ | "**绿色**（背景）和**深灰色/黑色**（货车主体和车轮）。其中绿色占据了画面的大部分面积，是最显眼的主色调。" |
| `green_van.png` | 文字内容与车辆实际颜色是否一致？ | "不一致。文字内容：GREEN VAN（绿色货车）。实际车辆颜色：深灰色/黑色。背景是绿色的，但车辆本身是深灰色，与文字描述的"GREEN"不符。这是一个典型的**文字与图像不匹配**的例子。" |
| `blue_truck.png` | 左上角的文字内容是什么？ | "左上角的文字内容是 **\"BLUE TRUCK\"**（蓝色卡车）。" |
| `green_van.png` | 车辆的主体颜色是什么？ | "根据图片，车辆（货车）的主体颜色是**深灰色/黑色**…虽然背景是绿色，且文字标注为"GREEN VAN"，但货车本身的主体颜色实际上是**深灰色（dark gray）**…" |

The model correctly **read the OCR text labels** *and* **distinguished them
from the underlying car silhouette colors** — proof that the image is being
attached to the vision call (not just the question text).

## Bugs found and fixed

1. **`questions generate --source image` was hitting the wrong endpoint.**
   The CLI POSTed `{sourceType:"image", imageIds:[...]}` to
   `/api/projects/{id}/generate-questions`, which is the **text** route and
   returns `404 'No valid text blocks found'` for image questions. The
   correct route is the `image-question-generation` background task at
   `/api/projects/{id}/tasks`. Fixed: `--source image` now creates that task,
   passes the vision-model config as `modelInfo`, and supports
   `--question-count N` (1–10) plus `--wait`.

2. **`datasets generate` for image questions silently produced hallucinated
   text-only answers.** The CLI POSTed `{questionId, model}` to
   `/api/projects/{id}/datasets`, which calls `generateDatasetForQuestion` —
   that function only handles **text** datasets and never attaches the image
   to the model call. Models without grounding then make up answers like
   "I cannot see your image…". The correct route is the
   `image-dataset-generation` background task, which calls
   `imageService.generateDatasetForImage` with the image attached. Fixed:
   `datasets generate --source image` now creates that task. The full
   model-config record is sent as `modelInfo` (server JSON-stringifies it).

Both fixes are covered by `tests/test_full_e2e.py::TestFullPipelineCase1`,
which now stubs the `/tasks` POST handler to side-effect-create one VQA
question per image when `taskType=="image-question-generation"`.

## Run metadata

- **Project:** `Y6O56uKh7Fxm` (`case1-vqa-1775649024`)
- **Model:** Kimi-K2.5 via Paratera (`type=vision`, temperature=0.4)
- **Images:** 4 PNGs synthesized via Pillow (256×192, flat-color silhouettes
  with text labels — see `input/make_images.py`)
- **Questions generated:** 6 (3 per image × 2 images that hadn't been
  processed in earlier failed runs)
- **Datasets generated:** 6 (one per question), 4 of which exported via
  `/image-datasets/export` (the other 2 were filtered by the export route's
  default `confirmedOnly=false` semantics — see TODO below)
- **Wall time:** ~5 s for question task, ~4 s for dataset task (small images)

## TODO (next refine pass)

- Add `easyds image-datasets list` and `easyds image-datasets export` to
  cover the `/image-datasets` and `/image-datasets/export-zip` routes
  natively (currently raw curl).
- Verify why `image-datasets/export` returned 4/6 even with `confirmedOnly=false`
  — possibly a cache/answer-mode filter on the underlying `lib/db/imageDatasets`
  helper.
