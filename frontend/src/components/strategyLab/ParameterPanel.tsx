import type { Preset, ParamValues } from "../../types/strategyLab";

const PARAM_META: Record<string, { label: string; step: number }> = {
  atr_period: { label: "ATR period", step: 1 },
  multiplier: { label: "Multiplier", step: 0.1 },
  lookback: { label: "Donchian lookback", step: 1 },
  initial_stop_loss_atr: { label: "Initial stop (× ATR)", step: 0.1 },
  trailing_stop_atr: { label: "Trailing stop (× ATR)", step: 0.1 },
  stop_loss_atr: { label: "Stop loss (× ATR)", step: 0.1 },
  take_profit_atr: { label: "Take profit (× ATR)", step: 0.5 },
  risk_percent: { label: "Risk per trade (%)", step: 0.05 },
  leverage: { label: "Leverage", step: 1 },
  initial_equity: { label: "Initial equity ($)", step: 100 },
};

type Props = {
  preset: Preset;
  values: ParamValues;
  onChange: (key: string, value: number | null) => void;
  onReset: () => void;
  disabled?: boolean;
};

export function ParameterPanel({ preset, values, onChange, onReset, disabled }: Props) {
  const keys = Object.keys(preset.default_parameters);

  return (
    <div className="sl-param-panel">
      <div className="sl-param-head">
        <span className="form-label">Parameters · {preset.strategy_name}</span>
        <button
          type="button"
          className="sl-link-btn"
          onClick={onReset}
          disabled={disabled}
        >
          Reset to defaults
        </button>
      </div>

      <div className="sl-param-grid">
        {keys.map((key) => {
          const meta = PARAM_META[key] ?? { label: key, step: 0.1 };
          const range = preset.allowed_ranges[key];
          const nullable = range?.nullable ?? false;

          if (key === "take_profit_atr") {
            return (
              <TakeProfitField
                key={key}
                label={meta.label}
                step={meta.step}
                min={range?.min}
                max={range?.max}
                value={values[key]}
                onChange={(v) => onChange(key, v)}
                disabled={disabled}
              />
            );
          }

          return (
            <label key={key} className="sl-field">
              <span className="sl-field-label">{meta.label}</span>
              <input
                type="number"
                className="sl-input"
                value={values[key] ?? ""}
                step={meta.step}
                min={range?.min}
                max={range?.max}
                disabled={disabled}
                onChange={(e) => {
                  const raw = e.target.value;
                  onChange(key, raw === "" && nullable ? null : Number(raw));
                }}
              />
              {range && (range.min !== undefined || range.max !== undefined) ? (
                <span className="sl-field-hint">
                  {range.min ?? "—"} … {range.max ?? "—"}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
    </div>
  );
}

function TakeProfitField({
  label,
  step,
  min,
  max,
  value,
  onChange,
  disabled,
}: {
  label: string;
  step: number;
  min?: number;
  max?: number;
  value: number | null;
  onChange: (value: number | null) => void;
  disabled?: boolean;
}) {
  const enabled = value !== null && value !== undefined;
  return (
    <label className="sl-field">
      <span className="sl-field-label">
        <input
          type="checkbox"
          className="sl-tp-toggle"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked ? (min ?? 16) : null)}
        />
        {label}
      </span>
      <input
        type="number"
        className="sl-input"
        value={enabled ? value : ""}
        step={step}
        min={min}
        max={max}
        placeholder="off"
        disabled={disabled || !enabled}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
      <span className="sl-field-hint">
        {enabled ? `${min ?? "—"} … ${max ?? "—"}` : "no take-profit"}
      </span>
    </label>
  );
}
