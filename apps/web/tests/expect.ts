// The few assertions the unit tests use, on top of Node's built-in assert module.
import assert from "node:assert/strict";
import { it } from "node:test";

function matches(actual: unknown, expected: unknown): boolean {
  if (expected === null || typeof expected !== "object") return Object.is(actual, expected);
  if (actual === null || typeof actual !== "object") return false;
  if (Array.isArray(expected)) {
    return Array.isArray(actual) && actual.length === expected.length && expected.every((e, i) => matches(actual[i], e));
  }
  return Object.entries(expected).every(([k, v]) => matches((actual as Record<string, unknown>)[k], v));
}

export function expect(actual: unknown) {
  return {
    toBe: (expected: unknown) => assert.ok(Object.is(actual, expected), `expected ${String(actual)} to be ${String(expected)}`),
    toEqual: (expected: unknown) => assert.deepStrictEqual(actual, expected),
    toMatchObject: (expected: object) => assert.ok(matches(actual, expected), `expected ${JSON.stringify(actual)} to match ${JSON.stringify(expected)}`),
    toBeNull: () => assert.strictEqual(actual, null),
    toBeGreaterThan: (n: number) => assert.ok((actual as number) > n, `expected ${String(actual)} to be greater than ${n}`),
    toHaveLength: (n: number) => assert.strictEqual((actual as { length: number }).length, n),
    toContain: (part: string) => assert.ok(String(actual).includes(part), `expected ${String(actual)} to contain ${part}`),
    toMatch: (pattern: RegExp) => assert.match(String(actual), pattern),
    not: { toThrow: () => assert.doesNotThrow(actual as () => unknown) },
  };
}

/** One test per row; "%s" in the name is replaced by the row's first value. */
export function each<T extends unknown[]>(rows: T[]) {
  return (name: string, fn: (...args: T) => void) => {
    for (const row of rows) it(name.replace("%s", String(row[0])), () => fn(...row));
  };
}
