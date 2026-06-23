import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveBoundaryDistance,
  resolveBoundaryRelation,
  resolveSupertrendBoundary,
} from "../src/components/strategyLab/mt5SignalDiagnostics.ts";

test("SuperTrend boundary falls back to supertrend_value", () => {
  const row = {
    buy_zone_level: null,
    supertrend_value: 4197.2,
    close_price: 4113.1,
    atr_value: 84.1 / 2.08,
    buy_zone_relation: "unknown",
  };

  assert.equal(resolveSupertrendBoundary(row), 4197.2);
  assert.equal(resolveBoundaryRelation(row, false), "below_buy_zone");
  const distance = resolveBoundaryDistance(row, false);
  assert.ok(Math.abs(Number(distance.price) - 84.1) < 1e-9);
  assert.ok(Math.abs(Number(distance.atr) - 2.08) < 1e-9);
});

test("explicit buy_zone_level remains preferred", () => {
  assert.equal(
    resolveSupertrendBoundary({
      buy_zone_level: 4200,
      supertrend_value: 4197.2,
    }),
    4200,
  );
});
