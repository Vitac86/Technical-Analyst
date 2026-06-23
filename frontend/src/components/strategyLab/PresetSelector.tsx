import type { Preset } from "../../types/strategyLab";

type Props = {
  presets: Preset[];
  selectedId: string;
  onSelect: (presetId: string) => void;
  disabled?: boolean;
};

export function PresetSelector({ presets, selectedId, onSelect, disabled }: Props) {
  return (
    <div className="sl-preset-grid">
      {presets.map((preset) => {
        const active = preset.preset_id === selectedId;
        return (
          <button
            key={preset.preset_id}
            type="button"
            className={`sl-preset-card${active ? " sl-preset-card-active" : ""}`}
            onClick={() => onSelect(preset.preset_id)}
            disabled={disabled}
            aria-pressed={active}
          >
            <div className="sl-preset-card-head">
              <span className="sl-preset-name">{preset.display_name}</span>
              <span className="sl-preset-tf">{preset.timeframe}</span>
            </div>
            <p className="sl-preset-desc">{preset.description}</p>
            <div className="sl-preset-tags">
              <span className="sl-tag">{preset.direction_mode}</span>
              <span className="sl-tag">{preset.exit_mode}</span>
              <span className="sl-tag">{preset.sizing_mode}</span>
              {preset.is_default ? (
                <span className="sl-tag sl-tag-default">default</span>
              ) : null}
            </div>
          </button>
        );
      })}
    </div>
  );
}
