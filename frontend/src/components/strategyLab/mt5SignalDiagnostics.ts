export interface BoundaryDiagnostics {
  buy_zone_level?: number | string | null;
  supertrend_value?: number | string | null;
  donchian_high?: number | string | null;
  close_price?: number | string | null;
  atr_value?: number | string | null;
  distance_to_buy_zone_price?: number | string | null;
  distance_to_buy_zone_atr?: number | string | null;
  distance_to_buy_zone_pct?: number | string | null;
  buy_zone_relation?: string | null;
}

function finiteNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = typeof value === "string" ? Number(value) : value;
  return Number.isFinite(number) ? number : null;
}

/** D fallback: prefer buy_zone_level, then the raw SuperTrend value. */
export function resolveSupertrendBoundary(
  diagnostics: BoundaryDiagnostics,
): number | string | null {
  return diagnostics.buy_zone_level ?? diagnostics.supertrend_value ?? null;
}

export function resolveReferenceBoundary(
  diagnostics: BoundaryDiagnostics,
  isDonchian: boolean,
): number | string | null {
  if (isDonchian) {
    return diagnostics.buy_zone_level ?? diagnostics.donchian_high ?? null;
  }
  return resolveSupertrendBoundary(diagnostics);
}

/**
 * Use the backend relation when present; otherwise derive it from close and
 * the resolved boundary. This prevents a stale v1.7.3 payload from displaying
 * "unknown" when the raw SuperTrend value is still available.
 */
export function resolveBoundaryRelation(
  diagnostics: BoundaryDiagnostics,
  isDonchian: boolean,
): string {
  const relation = diagnostics.buy_zone_relation;
  if (
    relation === "below_buy_zone" ||
    relation === "above_buy_zone" ||
    relation === "at_buy_zone"
  ) {
    return relation;
  }

  const close = finiteNumber(diagnostics.close_price);
  const boundary = finiteNumber(
    resolveReferenceBoundary(diagnostics, isDonchian),
  );
  if (close === null || boundary === null) return "unknown";

  const tolerance = Math.max(1e-9, Math.abs(boundary) * 1e-9);
  if (Math.abs(close - boundary) <= tolerance) return "at_buy_zone";
  return close < boundary ? "below_buy_zone" : "above_buy_zone";
}

export function resolveBoundaryDistance(
  diagnostics: BoundaryDiagnostics,
  isDonchian: boolean,
): {
  price: number | string | null;
  atr: number | string | null;
  percent: number | string | null;
} {
  const explicitPrice = finiteNumber(
    diagnostics.distance_to_buy_zone_price,
  );
  const explicitAtr = finiteNumber(diagnostics.distance_to_buy_zone_atr);
  const explicitPercent = finiteNumber(
    diagnostics.distance_to_buy_zone_pct,
  );
  const close = finiteNumber(diagnostics.close_price);
  const boundary = finiteNumber(
    resolveReferenceBoundary(diagnostics, isDonchian),
  );

  let price = explicitPrice;
  if (price === null && close !== null && boundary !== null) {
    price = Math.max(boundary - close, 0);
  }

  const atrValue = finiteNumber(diagnostics.atr_value);
  const atr =
    explicitAtr ??
    (price !== null && atrValue !== null && atrValue > 0
      ? price / atrValue
      : null);
  const percent =
    explicitPercent ??
    (price !== null && close !== null && close !== 0
      ? (price / close) * 100
      : null);

  return { price, atr, percent };
}
