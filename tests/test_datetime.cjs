const assert = require("node:assert/strict");
const test = require("node:test");

const { formatDateTime } = require("../frontend/js/date-time.js");

test("formats a UTC timestamp in Asia/Kolkata", () => {
  const formatted = formatDateTime("2026-11-12T12:00:00+00:00");
  assert.match(formatted, /12 Nov 2026/);
  assert.match(formatted, /5:30 pm/i);
});

test("converts UTC dates that fall on the previous local calendar day", () => {
  const formatted = formatDateTime("2026-11-12T20:00:00Z");
  assert.match(formatted, /13 Nov 2026/);
  assert.match(formatted, /1:30 am/i);
});

test("formats timestamps independently of the browser machine timezone", () => {
  const formatted = formatDateTime("2026-11-12T12:00:00Z");
  assert.doesNotMatch(formatted, /12:00 pm/i);
});
