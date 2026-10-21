// Exercise the actual browser pagination function without browser or mail access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../app/web/app.js"), "utf8");
const implementation = source.slice(source.indexOf("function emailPageRows("), source.indexOf("function renderEmails("));
const paginate = vm.runInNewContext(implementation + "; emailPageRows");
const emails = Array.from({ length: 53 }, (_, i) => ({
  id: String(i).padStart(3, "0"),
  received: new Date(Date.UTC(2026, 9, 1, 0, i)).toISOString(),
  done: i % 2 === 0,
  notes: "保留备注 " + i,
}));
const before = JSON.stringify(emails);
const pages = [1, 2, 3].map(page => paginate(emails, page, 20));
assert.equal(pages[0].items.length, 20);
assert.equal(pages[1].items.length, 20);
assert.equal(pages[2].items.length, 13);
assert.equal(pages[2].start, 41);
assert.equal(pages[2].end, 53);
assert.equal(new Set(pages.flatMap(p => Array.from(p.items, item => item.id))).size, 53);
assert.equal(pages[0].items[0], emails[52]); // Same record object, completion/notes intact.
assert.equal(JSON.stringify(emails), before); // Rendering must not sort or edit the source array.
for (let i = 1; i < pages.length; i++) {
  assert.ok(Date.parse(pages[i - 1].items.at(-1).received) >= Date.parse(pages[i].items[0].received));
}
for (const size of [10, 20, 50, 100]) assert.equal(paginate(emails, 1, size).items.length, Math.min(size, emails.length));
assert.equal(paginate(emails, 999, 20).current, 3);
assert.equal(paginate(emails.slice(0, 5), 3, 20).current, 1); // Completion/filter removes last page.
assert.equal(paginate([], 99, 20).start, 0);
assert.equal(paginate([], 99, 20).pages, 1);
assert.equal(paginate([], 99, 20).end, 0);
const mixedZones = [
  { id: "a", received: "2026-10-22T00:00:00+08:00" },
  { id: "b", received: "2026-10-21T17:00:00Z" },
];
assert.equal(paginate(mixedZones, 1, 10).items[0].id, "b");
console.log("分页检查通过：顺序、分界、条数、空结果、页码收缩及原始记录保留");
