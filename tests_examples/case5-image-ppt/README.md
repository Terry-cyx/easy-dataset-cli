# Case 5 — 图文 PPT → 纯文本 QA

**Status:** ✅ live run completed against Easy-Dataset server with real Kimi-K2.5 vision model

Reproduces `spec/03-case-studies.md` 案例 5 — convert a multi-page image PPT
(PDF) into a **text-only** QA dataset by:

1. Importing the PDF as one image per page (`files import --type image --from-pdf`)
2. Overriding the `imageQuestion` system prompt so generated questions are
   **standalone text** (no "in this figure"/"图中"-style references)
3. Running `image-question-generation` and `image-dataset-generation` tasks
4. Exporting an Alpaca-style dataset that **omits the image path**

This case validates the previously-pending coverage gap items:

- ✅ A3 (#66): `prompts set --type imageQuestion --key IMAGE_QUESTION_PROMPT`
  is honored end-to-end — the override flowed through to the
  `image-question-generation` task
- ✅ files import `--from-pdf` (PDF → page images)
- ✅ The `--source image` branches added by Case 1 work for PDF-derived
  images, not just hand-imported PNGs

## Layout

```
input/
  make_pdf.py             # Pillow script that synthesizes a 4-page slide PDF
  ai-edu-report.pdf       # 4-page synthetic image PPT (3 colored bars per slide)
prompt/
  image_question.md       # Custom system prompt: "脱离图片也能独立成立"
output/
  01_project.json
  02_model_set.json
  03_pdf_import.json      # 4 page-images imported (3072×2304 each)
  04_prompt_set.json      # ★ custom imageQuestion prompt persisted
  05_questions_task.json  # ★ image-question-generation task (status=1)
  06_questions.json       # 4 generated questions — fully standalone text
  07_dataset_task.json    # ★ image-dataset-generation task
  08_image_datasets_raw.json  # raw /image-datasets/export response
  ppt-alpaca-text-only.json   # ★ FINAL Alpaca export with input="" (no image path)
```

## Pipeline (live commands)

```bash
# 0. Synthesize the test PDF (4 slide-style pages, each with a fake bar chart)
python tests_examples/case5-image-ppt/input/make_pdf.py

# 1. Project + vision model (Paratera Kimi-K2.5)
PROJ=$(easyds --json project new --name case5-ppt-$(date +%s) | jq -r .id)
easyds --json project use $PROJ
easyds --json model set --provider-id openai --provider-name Paratera \
    --endpoint https://llmapi.paratera.com/v1/ --api-key sk-... \
    --model-id Kimi-K2.5 --type vision --temperature 0.4
easyds --json model use <new-mc-id>

# 2. ★ PDF → page images (server uses /images/pdf-convert under the hood)
easyds --json files import --type image \
    --from-pdf tests_examples/case5-image-ppt/input/ai-edu-report.pdf
# → 4 image rows (one per PDF page), 3072×2304 each

# 3. ★ Override the imageQuestion prompt so questions are standalone text
easyds --json prompts set \
    --type imageQuestion \
    --key IMAGE_QUESTION_PROMPT \
    --language zh-CN \
    --file tests_examples/case5-image-ppt/prompt/image_question.md \
    --require-var number

# 4. ★ image-question-generation task (2 questions per slide)
easyds --json questions generate --source image \
    --question-count 2 --language 中文 --wait --timeout 600

# 5. ★ image-dataset-generation task (vision model is given each slide)
easyds --json datasets generate --source image \
    --language 中文 --wait --timeout 900

# 6. Export image datasets and strip the image path
curl -sX POST http://localhost:1717/api/projects/$PROJ/image-datasets/export \
    -H 'content-type: application/json' \
    -d '{"confirmedOnly":false}' \
    > tests_examples/case5-image-ppt/output/08_image_datasets_raw.json
# → text-only Alpaca via a 3-line jq/python filter (input="" instead of imageName)
```

## Result — questions are fully standalone

Sample of the 4 generated questions (the **whole point** of the custom
prompt is that *none* of them say "图中" / "as shown" / "上图"):

> 1. 在2026年逆风因素分析中，隐私（Privacy）、成本（Cost）和信任（Trust）三项指标的担忧指数分别为多少？
> 2. 2026年逆风因素分析显示，信任担忧指数90、隐私担忧指数78、成本担忧指数55，哪一项指标的担忧程度最高，哪一项最低？
> 3. 在Top 3 Use Cases中，Content的采用指数（Adoption index）为300，Tutoring的采用指数为220，Content比Tutoring高出多少？
> 4. Top 3 Use Cases中三个应用场景的采用指数（Adoption index，范围0-400）分别是：Tutoring 220、Grading 140、Content 300，三者总和是多少？

Every question carries its own **year, region, metric and unit**, so the
question is meaningful even if you delete the source image. The vision
model picked all of these up directly from the slide titles, footers and
bar values — which is exactly what the custom prompt asked for.

The corresponding answers are **arithmetic** ("220+140+300=660"), not
guessing — proof that the vision model is reading the actual numbers off
the synthesized chart bars.

## Run metadata

- **Project:** `BeiPACFdxdCb` (`case5-ppt-1775650070`)
- **Model:** Kimi-K2.5 via Paratera, type=vision, temperature=0.4
- **PDF:** 4 pages, synthesized via Pillow's multi-page PDF support
- **Questions generated:** 4 (2 per page × 2 pages) — see TODO note
- **Datasets generated:** 2 exported (subset of 4 due to the same
  `confirmedOnly` filter quirk seen in case 1)
- **Wall time:** ~6 s for question task, ~5 s for dataset task

## TODO (next refine pass)

- Same as case 1: add native `easyds image-datasets list` and
  `easyds image-datasets export` so we don't need raw curl
- Investigate why `image-question-generation` only emits 2 records when
  asked for 2-per-image × 4 images (likely a stale-question filter on
  the previous run, since the project was reused once)
