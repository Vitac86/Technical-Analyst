import type { CostScenario, CustomCosts } from "../../types/strategyLab";

const SCENARIOS = ["Base", "Conservative", "Stress", "Custom"] as const;

const CUSTOM_FIELDS: { key: keyof CustomCosts; label: string }[] = [
  { key: "fixed_spread_points", label: "Spread (pts)" },
  { key: "slippage_points", label: "Slippage (pts/side)" },
  { key: "commission_per_lot_round_turn", label: "Commission ($/lot)" },
  { key: "swap_long_per_lot_per_day", label: "Swap long ($/lot/day)" },
  { key: "swap_short_per_lot_per_day", label: "Swap short ($/lot/day)" },
];

type Props = {
  scenario: string;
  onScenario: (scenario: string) => void;
  customCosts: CustomCosts;
  onCustomChange: (key: keyof CustomCosts, value: number) => void;
  catalogue: CostScenario[];
  disabled?: boolean;
};

export function CostScenarioSelector({
  scenario,
  onScenario,
  customCosts,
  onCustomChange,
  catalogue,
  disabled,
}: Props) {
  const builtin = catalogue.find((c) => c.name === scenario);

  return (
    <div className="sl-cost-panel">
      <span className="form-label">Cost scenario</span>
      <div className="timeframe-selector">
        {SCENARIOS.map((name) => (
          <button
            key={name}
            type="button"
            className={`tf-btn${scenario === name ? " tf-btn-active" : ""}`}
            onClick={() => onScenario(name)}
            disabled={disabled}
          >
            {name}
          </button>
        ))}
      </div>

      {scenario === "Custom" ? (
        <div className="sl-cost-custom">
          {CUSTOM_FIELDS.map((field) => (
            <label key={field.key} className="sl-field">
              <span className="sl-field-label">{field.label}</span>
              <input
                type="number"
                className="sl-input"
                value={customCosts[field.key]}
                step={1}
                disabled={disabled}
                onChange={(e) => onCustomChange(field.key, Number(e.target.value))}
              />
            </label>
          ))}
        </div>
      ) : builtin ? (
        <p className="sl-cost-hint">
          Spread {builtin.fixed_spread_points} pts · slippage {builtin.slippage_points}{" "}
          pts/side · commission ${builtin.commission_per_lot_round_turn}/lot · swap long $
          {builtin.swap_long_per_lot_per_day}/lot/day
        </p>
      ) : null}
    </div>
  );
}
