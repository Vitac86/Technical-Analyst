// Small display formatters shared by the Strategy Lab UI.

export function fmtMoney(value: number | null | undefined, dp = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function fmtPct(value: number | null | undefined, dp = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(dp)}%`;
}

export function fmtNum(value: number | null | undefined, dp = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(dp);
}

/** Profit factor: null means no losing trades (∞) when there is profit. */
export function fmtProfitFactor(
  value: number | null | undefined,
  netProfit?: number | null,
  trades?: number,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    if ((netProfit ?? 0) > 0 && (trades ?? 0) > 0) return "∞";
    return "—";
  }
  return value.toFixed(2);
}

export function fmtRatePct(fraction: number | null | undefined, dp = 1): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) {
    return "—";
  }
  return `${(fraction * 100).toFixed(dp)}%`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 16).replace("T", " ");
}

export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  if (value > 0) return "sl-pos";
  if (value < 0) return "sl-neg";
  return "";
}
