# Case 2 — Chinese Review Sentiment Classification

**Status:** ✅ live run completed against Easy-Dataset server with real Kimi-K2.5 LLM

Reproduces 案例 2 (评论情感分类数据集) from the official Easy-Dataset docs,
end-to-end through `easyds`.

## Layout

```
input/
  reviews.md           # 8 Chinese product/service reviews, separated by '---------'
prompt/
  (none — uses the built-in label-template prompt with --label-set)
output/
  01_upload.json       # files upload response
  02_split.json        # chunks split (custom-separator) response
  03_chunks.json       # 8 chunks, one per review
  04_template_apply.json
  05_questions.json    # 8 templated questions, one per chunk
  06_generate.json     # batch dataset generation response
  07_datasets.json     # 8 dataset rows with label answers
  08_export.json       # export run metadata
  sentiment-alpaca.json   # ★ FINAL — 8 Alpaca records with the labels
  template_id.txt
  run_meta.txt
```

## Pipeline (live commands actually run)

```bash
# 1. Project + model (per-project model config required)
PROJ=$(easyds --json project new --name case2-sentiment-$(date +%s) | jq -r .id)
easyds --json project use $PROJ
easyds --json model set --provider-id openai --provider-name Paratera \
    --endpoint https://llmapi.paratera.com/v1/ --api-key sk-... \
    --model-id Kimi-K2.5 --temperature 0.0
easyds --json model use <new-mc-id>

# 2. Upload + custom-separator chunking (1 chunk per review)
easyds --json files upload tests_examples/case2-sentiment/input/reviews.md
easyds --json chunks split --file reviews.md \
    --separator '---------' \
    --content-file tests_examples/case2-sentiment/input/reviews.md
# → 8 chunks

# 3. Create labeled template + apply to all chunks
TMPL=$(easyds --json questions template create \
    --question "请判断这条评论的情感倾向" \
    --source-type text --type label \
    --label-set "正面,负面,中性" | jq -r .template.id)
easyds --json questions template apply $TMPL
# → 8 templated questions

# 4. Generate the labels
easyds --json datasets generate --language 中文
# → 8 dataset rows

# 5. Export
easyds --json export run \
    -o tests_examples/case2-sentiment/output/sentiment-alpaca.json \
    --format alpaca --include-chunk --all --overwrite
```

## Result

8 Alpaca records, **all 8 labels match the expected sentiment** of the input
reviews when sampled by hand:

| Review (truncated) | Label |
|---|---|
| 红烧肉绝了，服务热情，下次再来 | 正面 |
| 外卖等两小时，菜凉透 | 负面 |
| 耳机音质可以，戴久耳朵疼 | 中性 |
| 书籍排版精美，内容浅显 | 中性 |
| 酒店隔音差，再也不订 | 负面 |
| 快递小哥负责，五星好评 | 正面 |
| 课程口音重，整体一般 | 中性 |
| 手机外观酷，续航拉胯 | 负面 |

## CLI capabilities verified

- ✅ `chunks split --separator --content-file` (custom separator routing)
- ✅ `questions template create --source-type text --type label --label-set` (Task A2)
- ✅ `questions template apply <id>` materializing 1 question per chunk
- ✅ `datasets generate` constraining answers to label set
- ✅ `export run --format alpaca --include-chunk`

## Server quirk noted

`--include-chunk` exports `chunkContent: ""` for templated questions because
the server doesn't denormalize chunk content into the dataset row in this
code path. **The chunkName field is set**, so a downstream join against
`chunks list` recovers the text. Worth a follow-up issue upstream.

## Run metadata

- **Project:** `xtKa6zKzcDP7` (`case2-sentiment-1775637...`)
- **Model:** Kimi-K2.5 via Paratera, temperature=0
- **Wall time:** ~80 seconds for 8 chunks (text is short, label answers are 1 token)
