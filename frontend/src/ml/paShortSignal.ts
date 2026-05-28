/**
 * PA SHORT signal orchestrator.
 *
 * Loads the pa_short_v0 model JSON once (cached in memory) and computes
 * SHORT risk probability from the currently loaded chart candles.
 *
 * No backend calls. No candle or prediction persistence.
 * Returns clearly labelled "research only" result at all times.
 */

import type { MoexCandle } from '../api/moexDirect';
import { loadCatBoostJsonModel, evaluateCatBoostBinary } from './catboostJsonRuntime';
import type { LoadedModel } from './catboostJsonRuntime';
import { calculatePaFeatures, PA_FEATURE_NAMES } from './priceActionFeatures';

// ── Types ─────────────────────────────────────────────────────────────────────

export type PaRiskLevel = 'none' | 'watch' | 'elevated' | 'high';

export interface PaShortSignalResult {
  available: boolean;
  modelId: string;
  probabilityShort: number | null;
  riskLevel: PaRiskLevel;
  thresholdUsed: number;
  message: string;
  backtestValidated: false;
  reason?: string;
}

interface PaManifest {
  modelId: string;
  featureCount: number;
  featureNames: string[];
  requiredMinCandles: number;
  thresholds: { watch: number; elevated: number; high: number };
  labelConfig: {
    horizonCandles: number;
    takeProfitPct: number;
    stopLossPct: number;
  };
  backtestStatus: { note: string };
  classNames: string[];
}

// ── Module-level cache ────────────────────────────────────────────────────────

type ModelState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; manifest: PaManifest; loaded: LoadedModel & { ok: true } }
  | { status: 'error'; reason: string };

let _state: ModelState = { status: 'idle' };
let _loadPromise: Promise<void> | null = null;
let _onReadyCallbacks: (() => void)[] = [];

function _startLoad(): Promise<void> {
  if (_loadPromise) return _loadPromise;
  _state = { status: 'loading' };
  _loadPromise = (async () => {
    try {
      const [manifestRes, modelResult] = await Promise.all([
        fetch('/models/pa_short_v0/manifest.json').then(r => {
          if (!r.ok) throw new Error(`manifest HTTP ${r.status}`);
          return r.json() as Promise<PaManifest>;
        }),
        loadCatBoostJsonModel('/models/pa_short_v0/model.json'),
      ]);

      if (!modelResult.ok) {
        _state = { status: 'error', reason: `Model load error: ${modelResult.reason}` };
        return;
      }

      // Validate manifest vs computed feature list
      const manifestNames: string[] = manifestRes.featureNames ?? [];
      const tsNames: string[] = [...PA_FEATURE_NAMES];
      if (manifestNames.length !== tsNames.length) {
        _state = {
          status: 'error',
          reason: `Feature count mismatch: manifest=${manifestNames.length}, TS=${tsNames.length}`,
        };
        return;
      }
      for (let i = 0; i < tsNames.length; i++) {
        if (manifestNames[i] !== tsNames[i]) {
          _state = {
            status: 'error',
            reason: `Feature name mismatch at [${i}]: manifest="${manifestNames[i]}", TS="${tsNames[i]}"`,
          };
          return;
        }
      }

      _state = { status: 'ready', manifest: manifestRes, loaded: modelResult };
      // Notify any waiting callers
      for (const cb of _onReadyCallbacks) cb();
      _onReadyCallbacks = [];
    } catch (err) {
      _state = { status: 'error', reason: `Model init failed: ${String(err)}` };
    }
  })();
  return _loadPromise;
}

/**
 * Start loading the model in the background.
 * Call once on app mount; resolves when the model is ready or has failed.
 * Returns a promise that resolves after loading (success or failure).
 */
export async function initPaModel(): Promise<void> {
  return _startLoad();
}

/**
 * Register a callback that fires once when the model finishes loading.
 * Used by React components to trigger a re-render when the model becomes ready.
 */
export function onPaModelReady(cb: () => void): void {
  if (_state.status === 'ready') {
    cb();
  } else if (_state.status !== 'error') {
    _onReadyCallbacks.push(cb);
  }
}

// ── Signal computation ────────────────────────────────────────────────────────

const RESEARCH_NOTE = 'Research model. Backtest not yet profitable.';

function makeUnavailable(reason: string): PaShortSignalResult {
  return {
    available: false,
    modelId: 'pa_short_v0',
    probabilityShort: null,
    riskLevel: 'none',
    thresholdUsed: 0,
    message: reason,
    backtestValidated: false,
    reason,
  };
}

/**
 * Synchronously compute the PA SHORT signal from the current candle set.
 * Returns immediately — model loading is handled in the background.
 * Call initPaModel() once on mount to start the load.
 */
export function computePaShortSignal(candles: MoexCandle[]): PaShortSignalResult {
  // Start load on first call
  if (_state.status === 'idle') {
    void _startLoad();
    return makeUnavailable('Loading experimental model…');
  }

  if (_state.status === 'loading') {
    return makeUnavailable('Loading experimental model…');
  }

  if (_state.status === 'error') {
    return makeUnavailable(`Experimental model unavailable: ${_state.reason}`);
  }

  const { manifest, loaded } = _state;

  if (!Array.isArray(candles) || candles.length === 0) {
    return makeUnavailable('No candle data');
  }

  // Compute features
  const featResult = calculatePaFeatures(candles);
  if (!featResult.available) {
    return makeUnavailable(featResult.reason);
  }

  // Final feature-count guard (belt-and-suspenders)
  if (featResult.features.length !== manifest.featureCount) {
    return makeUnavailable(
      `Experimental model unavailable: feature mismatch (${featResult.features.length} vs ${manifest.featureCount})`,
    );
  }

  // Evaluate model
  const probShort = evaluateCatBoostBinary(loaded, featResult.features);

  // Map probability to risk level
  const { watch, elevated, high } = manifest.thresholds;
  let riskLevel: PaRiskLevel = 'none';
  if (probShort >= high)     riskLevel = 'high';
  else if (probShort >= elevated) riskLevel = 'elevated';
  else if (probShort >= watch)    riskLevel = 'watch';

  const messages: Record<PaRiskLevel, string> = {
    none:     'No elevated SHORT risk',
    watch:    'SHORT risk watch',
    elevated: 'Elevated SHORT risk',
    high:     'High SHORT risk',
  };

  return {
    available: true,
    modelId: manifest.modelId,
    probabilityShort: probShort,
    riskLevel,
    thresholdUsed: watch,
    message: messages[riskLevel],
    backtestValidated: false,
    reason: RESEARCH_NOTE,
  };
}

/** Current model load state — for diagnostic display. */
export function getPaModelStatus(): string {
  switch (_state.status) {
    case 'idle':    return 'not started';
    case 'loading': return 'loading';
    case 'ready':   return 'ready';
    case 'error':   return `error: ${_state.reason}`;
  }
}
