# SOInsight classifier model

A purpose-built Ollama model for SOInsight's classification step: the taxonomy
baked into the system prompt, deterministic sampling, and — most importantly — a
context window large enough for the prompt the application actually sends.

**This is a packaged and parameterised model, not fine-tuned weights.** It needs
no GPU and builds in seconds. If you want genuinely trained weights afterwards,
[Fine-tuning](#fine-tuning-optional) below is the path, and the dataset exporter
that makes it possible is included.

## Why this exists

SOInsight sends a large batch prompt: the taxonomy, 16 few-shot examples, and up
to 20 questions with 300 characters of body each. Measured:

| Prompt | Size |
|---|---|
| Full batch (20 questions) | ~14,200 chars — **~3,561 tokens** |
| Single-question retry | ~1,840 chars — ~460 tokens |

Ollama applies a default context window unless a model sets one, and that default
is at or below 3,561 tokens depending on version. An overflowing prompt is
truncated **from the front** — which is where the taxonomy and the instructions
live. The model is then asked to classify against categories it can no longer
see, returns something invalid, and SOInsight's own guard rails take over: the
per-question retry runs, and on a second failure the question falls back to
`Misuse / Noise`.

The visible symptom is questions landing in noise for no obvious reason, and a
slow Analysis run (every failure costs an extra model call). Setting `num_ctx`
explicitly removes the whole failure mode. The other parameters make repeated
runs over identical data produce identical counts.

## Build

```bash
./models/build.sh                 # default base: qwen2.5:3b-instruct
./models/build.sh llama3.2:3b     # or pick another base
```

The script pulls the base, regenerates the Modelfile from
`backend/app/taxonomy.py`, builds `soinsight-classifier`, and smoke-tests it —
asserting the reply is valid JSON *and* a valid taxonomy pair.

Re-running is safe; `ollama create` replaces the tag.

> The Modelfile is generated, never hand-edited. The taxonomy is a closed set
> that the app validates against, so a drifted copy silently costs accuracy.
> Change `backend/app/taxonomy.py` and rebuild.

## Use it

Settings → **Classification model** → `soinsight-classifier`, or set it in
`backend/.env`:

```
OLLAMA_MODEL=soinsight-classifier
```

Changing the model also invalidates cached remediation guides — their content
hash includes the model name — so the next **Update guide** regenerates them.

## Choosing a base

| Base | Size | Notes |
|---|---|---|
| `qwen2.5:3b-instruct` *(default)* | ~2 GB | Best strict-JSON adherence at this size, which is the whole job. |
| `llama3.2:3b` | ~2 GB | Slightly weaker on rigid enum output; fine if you already have it. |
| `qwen2.5:7b-instruct` | ~4.7 GB | Noticeably better on the subtler categories if you have the headroom. |
| `llama3.2:1b` | ~1.3 GB | Fastest, but struggles to hold 29 valid pairs — expect more noise fallbacks. |

Don't switch on vibes — score it (below).

## Measure before you trust it

The repo has an eval harness, and your own database is the best source of
labelled data: every Analysis run has already produced
question → category pairs in your taxonomy, on your products.

```bash
cd backend

# Export your reviewed classifications as eval data
python -m eval.export_dataset --eval-csv eval/from_db.csv --exclude-noise --min-confidence 0.7

# Score the currently configured model against them
python -m eval --csv eval/from_db.csv --out eval_report.md
```

`eval_report.md` gives per-category precision/recall/F1 and a confusion matrix.
Run it once with your current model, once with `soinsight-classifier`, and
compare. The exporter also prints per-category counts and flags any category
with under 10 examples — those are the ones a report will over- or under-state.

The labels come from a model, so treat them as *consistency* data unless you have
spot-checked them. `--min-confidence` is a crude filter for that; reviewing a
sample is better.

## Publishing to Ollama

`ollama.com` models live under **your** namespace and pushing is public and hard
to undo, so this is a step to run yourself:

```bash
ollama create <your-username>/soinsight-classifier -f models/Modelfile
ollama push <your-username>/soinsight-classifier
```

Requires a signed-in Ollama account (`ollama login`) whose username matches the
tag prefix. Anyone can then `ollama pull <your-username>/soinsight-classifier`.

Two things to check first, since a push is public:

- The Modelfile embeds your taxonomy, including category names. If those name
  internal products or programmes, publish privately or rename them first.
- The base model's licence travels with a derived model — Qwen2.5 and Llama 3.2
  each carry their own terms.

For internal-only distribution, skip the push and share the repo: `build.sh`
reproduces the identical model from source in seconds.

## Fine-tuning (optional)

Packaging fixes prompt-level failures. It cannot teach the model your
domain — that a question mentioning a specific internal service is
*Adoption / Migration* rather than *Technical*. That needs training, a GPU, and
enough labelled examples.

The exporter produces training data in chat format, where the assistant turn is
the exact JSON contract the classifier expects back:

```bash
cd backend
python -m eval.export_dataset \
  --jsonl train.jsonl \
  --eval-csv holdout.csv \
  --split 0.2 \
  --exclude-noise --min-confidence 0.8
```

`--split` holds back a fraction for evaluation, so you are not scoring a tuned
model on the data it memorised. Without it, both files get every row.

Then, on a GPU box:

1. LoRA fine-tune the base with your `train.jsonl` — [Unsloth](https://github.com/unslothai/unsloth)
   and [Axolotl](https://github.com/axolotl-ai-cloud/axolotl) both take this
   format directly. A 3B model with a few thousand examples is well under an hour
   on one consumer GPU.
2. Merge the adapter and convert to GGUF (`llama.cpp`'s `convert_hf_to_gguf.py`),
   then quantise (q4_K_M is a good default).
3. Point a Modelfile at the result and keep every `PARAMETER` line from the
   generated one — a tuned model with a 2048-token context has the same
   truncation problem as an untuned one:

   ```
   FROM ./soinsight-tuned-q4_K_M.gguf
   PARAMETER num_ctx 8192
   PARAMETER temperature 0
   ...
   ```
4. Score it against `holdout.csv` with `python -m eval` before switching.

Realistic guidance on volume: below ~500 labelled examples, fine-tuning tends to
underperform the packaged model here — you mostly teach it to imitate the model
that produced the labels. Above a few thousand *reviewed* examples it starts to
pay off, most visibly on the categories a general model conflates.

## Files

| File | Purpose |
|---|---|
| `generate_modelfile.py` | Generates the Modelfile from `backend/app/taxonomy.py`. Refuses a `num_ctx` too small for the batch prompt. |
| `build.sh` | Pull base → generate → `ollama create` → smoke test. |
| `Modelfile` | Generated output. Rebuilt, not edited. |
| `backend/eval/export_dataset.py` | Database → eval CSV and fine-tuning JSONL. |
