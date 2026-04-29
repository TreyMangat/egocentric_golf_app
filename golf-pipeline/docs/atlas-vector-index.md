# Atlas vector search index

Used by `find_similar_swings` (V1.5) for "closest similar swing" lookup.

## Create the index

In Atlas Cloud → cluster → **Atlas Search** → **Create Search Index** → JSON editor.

- Database: `golf_pipeline`
- Collection: `swings`
- Index name: `swing_embeddings`

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 256,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "userId"
    },
    {
      "type": "filter",
      "path": "capture.club"
    },
    {
      "type": "filter",
      "path": "capture.view"
    }
  ]
}
```

## Embedding choice (V1.5)

Two reasonable options for the 256-d swing embedding, in increasing complexity:

1. **Hand-rolled summary vector** — concat normalized phase timings, the 12 Tier-1 metrics, and a downsampled wrist-trajectory PCA. Free, deterministic, surprisingly good for "find a similar swing of mine".
2. **Learned encoder** — train a small temporal CNN/Transformer on pose timeseries with a contrastive loss (positive = same swing rep, negative = different). Real ML rep, V2 territory.

Start with option 1. Save the trained-encoder version for the Hugging Face fine-tuning rep in V2.

## Filtering by club / view

For "closest pro match" we'll want to filter by club (driver-vs-driver only). Filters are declared in the index above and used at query time:

```python
pipeline = [{
    "$vectorSearch": {
        "index": "swing_embeddings",
        "path": "embedding",
        "queryVector": embedding,
        "numCandidates": 200,
        "limit": 6,
        "filter": {
            "userId": user_id,
            "capture.club": "driver"
        }
    }
}]
```
