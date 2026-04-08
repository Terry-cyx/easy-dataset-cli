# Workflow — Image VQA (Visual Question Answering)

Build a Visual-Question-Answering dataset from a local directory of images. Reproduces spec/03-case-studies §案例 1 (汽车图片识别).

## When to use this

- You have a folder of images (or a PDF you want auto-converted to images)
- You want a vision LLM (GPT-4o, Claude 3.5 Sonnet vision, Qwen-VL etc.) to generate questions about each image
- You want both free-text and structured (label / JSON-schema) outputs

## Recipe

```bash
# 1. Create the project
easyds --json project new --name car-vqa

# 2. Register a VISION model (--type vision so the image pipeline picks it)
mc=$(easyds --json model set \
        --provider-id openai \
        --endpoint https://api.openai.com/v1 \
        --api-key sk-... \
        --model-id gpt-4o \
        --type vision \
     | jq -r .id)
easyds --json model use "$mc"

# 3a. Bulk-import a local directory of images (server unpacks the zip)
easyds --json files import --type image --dir ./cars/

# 3b. OR convert a PDF to per-page images server-side
# easyds --json files import --type image --from-pdf ./catalog.pdf

# 4. Create three question templates of three answer types
easyds --json questions template create \
    --question "请描述这张图中的车辆。" \
    --source-type image --type text

easyds --json questions template create \
    --question "这辆车是什么品牌？" \
    --source-type image --type label \
    --label-set "宝马,奔驰,奥迪,丰田,其他"

easyds --json questions template create \
    --question "提取车辆结构化信息" \
    --source-type image --type json-schema \
    --schema-file ./schemas/car.json \
    --auto-generate

# 5. Generate VQA questions for every image. --source image auto-resolves the
#    vision model registered in step 2.
easyds --json questions generate --source image --ga

# 6. Generate the answers
easyds --json datasets generate

# 7. (Optional) Inspect and prune noisy images
easyds --json files list-images
easyds --json files prune --id img-3 --id img-7

# 8. Export
easyds --json export run -o ./vqa.json --format alpaca \
    --include-image-path \
    --all --overwrite
```

## Notes

- The vision model must be registered with `--type vision`. The CLI's `questions generate --source image` looks up the project's active vision model — there's no override flag.
- `--include-image-path` in `export run` adds the source image path to each record so the trainer can find it.
- Templates of type `label` need `--label-set "a,b,c"`; type `json-schema` needs `--schema-file path/to/schema.json`.
- Image VQA still respects [Rule 2](../06-operating-rules.md) — `model use` after `model set` is mandatory, otherwise the image endpoint can't find your vision model.
