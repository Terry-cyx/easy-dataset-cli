# Workflow — Domain-Tree Editing

Manually curate the LLM-built domain tree before / after question generation.

## When to use this

- The auto-built domain tree is wrong or incomplete
- You need to merge / split / reparent categories
- You want to look up which questions are tagged with a specific label

## Recipe

```bash
# 1. Build initial chunks + domain tree
easyds chunks split --file paper.pdf

# 2. Inspect the tree
easyds tags list             # nested view (recommended)
easyds tags list --flat      # flattened label list

# 3. Add a manual category
easyds tags create "经典力学" --parent "物理学"
easyds tags create "牛顿定律" --parent "经典力学"

# 4. Rename or reparent existing tags
easyds tags rename "电磁学" "电动力学"
easyds tags move "电动力学" --parent "经典力学"

# 5. Look up all questions tagged with a label
easyds --json tags questions "牛顿定律"

# 6. Delete an unwanted leaf
easyds tags delete "动量守恒"
```

## Notes

- The domain tree is hierarchical (`Tags` table with self-referential `parentId`).
- `tags create` requires `--parent` for non-root tags.
- `tags delete` only works on leaves — delete children first.
- `tags rename` and `tags move` propagate to all questions/datasets that reference the label — no manual cascade needed.
- For programmatic editing, `easyds --json tags list` returns the full nested structure.
