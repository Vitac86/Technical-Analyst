/**
 * Lightweight CatBoost JSON model evaluator for the browser / Android WebView.
 *
 * Supports:
 *   - Symmetric (oblivious) trees only — the CatBoost default.
 *   - Float / numeric features only — no categorical features.
 *   - Binary classification (Logloss) with sigmoid output.
 *
 * No external dependencies. Designed for the pa_short_v0 model JSON export.
 *
 * Format reference (from observed CatBoost JSON export):
 *   {
 *     "oblivious_trees": [{
 *       "splits": [{ "float_feature_index": N, "border": B, "split_type": "FloatFeature" }],
 *       "leaf_values": [...],       // 2^depth scalar values
 *       "leaf_weights": [...]
 *     }],
 *     "scale_and_bias": [scale, [bias]] or [[scale], [bias]]
 *   }
 */

export interface CatBoostSplit {
  float_feature_index: number;
  border: number;
  split_type: string;
}

export interface CatBoostTree {
  splits: CatBoostSplit[];
  leaf_values: number[];
  leaf_weights?: number[];
}

export interface CatBoostJsonModel {
  oblivious_trees: CatBoostTree[];
  scale_and_bias: unknown;
  features_info?: unknown;
  model_info?: unknown;
}

export type LoadedModel =
  | { ok: true; model: CatBoostJsonModel; scale: number; bias: number }
  | { ok: false; reason: string };

/** Fetch and parse a CatBoost JSON model from a URL. */
export async function loadCatBoostJsonModel(url: string): Promise<LoadedModel> {
  let raw: unknown;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    raw = await res.json();
  } catch (err) {
    return { ok: false, reason: `Fetch failed: ${String(err)}` };
  }

  const model = raw as Record<string, unknown>;

  if (!Array.isArray(model.oblivious_trees)) {
    return { ok: false, reason: 'Invalid model JSON: missing oblivious_trees' };
  }

  const trees = model.oblivious_trees as unknown[];
  for (let i = 0; i < trees.length; i++) {
    const t = trees[i] as Record<string, unknown>;
    if (!Array.isArray(t.splits) || !Array.isArray(t.leaf_values)) {
      return { ok: false, reason: `Tree ${i} missing splits or leaf_values` };
    }
    if (t.leaf_values.length !== (1 << t.splits.length)) {
      return {
        ok: false,
        reason: `Tree ${i}: leaf_values length ${t.leaf_values.length} != 2^${t.splits.length}`,
      };
    }
  }

  // Parse scale_and_bias: CatBoost exports as [scale, [bias]] or [[scale], [bias]]
  let scale = 1;
  let bias = 0;
  const sb = model.scale_and_bias;
  if (Array.isArray(sb)) {
    const s = sb[0];
    const b = sb[1];
    scale = Array.isArray(s) ? (s[0] ?? 1) : typeof s === 'number' ? s : 1;
    bias  = Array.isArray(b) ? (b[0] ?? 0) : typeof b === 'number' ? b : 0;
  }

  return {
    ok: true,
    model: model as unknown as CatBoostJsonModel,
    scale,
    bias,
  };
}

/**
 * Evaluate a loaded CatBoost binary model on a numeric feature vector.
 * Returns P(positive class) = P(SHORT) in [0, 1].
 *
 * Feature values must be in the exact order the model was trained on.
 * Passing NaN features is supported at the evaluator level (NaN > border = false),
 * but the orchestrator should reject inputs with NaN features before calling here.
 */
export function evaluateCatBoostBinary(
  loaded: LoadedModel & { ok: true },
  features: number[],
): number {
  const { model, scale, bias } = loaded;
  let rawSum = 0;

  for (const tree of model.oblivious_trees) {
    let leafIdx = 0;
    const splits = tree.splits;
    for (let i = 0; i < splits.length; i++) {
      const sp = splits[i];
      if (features[sp.float_feature_index] > sp.border) {
        leafIdx |= 1 << i;
      }
    }
    rawSum += tree.leaf_values[leafIdx];
  }

  const logit = rawSum * scale + bias;
  // Sigmoid → probability
  return 1 / (1 + Math.exp(-logit));
}
