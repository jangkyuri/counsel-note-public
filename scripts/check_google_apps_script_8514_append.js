const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = process.argv[2] || path.join(__dirname, "google_apps_script_doPost.js");
const extension = path.join(__dirname, "google_apps_script_8514_append.js");
const secret = "a".repeat(64);
const docId = "qa-synthetic-doc-8514";
const rowDate = "2026-11-22\n\uacfc\ubaa9: \uacf5\uc2182";
const existing = "\uc9c4\ub3c4: QA-KEEP progress\n\uc219\uc81c:\n- QA-KEEP homework";
const notes = { text: existing, appends: 0, appendParagraph(text) {
  this.text += (this.text ? "\n" : "") + text;
  this.appends++;
}, getText() { return this.text; } };
const cell = (text) => ({ ...notes, text, appends: 0 });
const headers = [cell("\uc0c1\ub2f4\uc77c\uc790"), cell("\uc138\ubd80\ub0b4\uc6a9")];
const label = cell(rowDate);
const row = (cells) => ({ getNumCells: () => cells.length, getCell: (i) => cells[i] });
const table = [row(headers), row([label, notes])];
const tableView = { getNumRows: () => table.length, getRow: (i) => table[i] };
const props = new Map([
  ["CLG8514_APPEND_SECRET", secret], ["CLG8514_APPEND_ALLOWED_DOC_IDS", JSON.stringify([docId])],
]);
let opened = 0;
let saved = 0;
let docTitle = "CLG QA-SYNTHETIC test document";
const context = vm.createContext({
  ContentService: { MimeType: { JSON: "json" }, createTextOutput: (s) => ({
    setMimeType: () => JSON.parse(s),
  }) },
  PropertiesService: { getScriptProperties: () => ({
    getProperty: (key) => props.get(key), setProperty: (key, val) => props.set(key, val),
    deleteProperty: (key) => props.delete(key),
  }) },
  LockService: { getScriptLock: () => ({
    tryLock: () => true, waitLock() {}, releaseLock() {},
  }) },
  Utilities: {
    DigestAlgorithm: { SHA_256: "sha256" }, Charset: { UTF_8: "utf8" },
    computeDigest: (_, value) => [...crypto.createHash("sha256").update(value).digest()],
    computeHmacSha256Signature: (value, key) => [...crypto.createHmac("sha256", key).update(value).digest()],
    base64DecodeWebSafe: (value) => [...Buffer.from(value, "base64url")],
    newBlob: (bytes) => ({ getDataAsString: () => Buffer.from(bytes).toString("utf8") }),
    getUuid: () => crypto.randomUUID(), sleep() {},
  },
  DocumentApp: { openById: (id) => {
    assert.equal(id, docId);
    opened++;
    return {
      getName: () => docTitle, getTabs: () => [{getChildTabs: () => []}],
      getBody: () => ({ getTables: () => [tableView] }), saveAndClose: () => { saved++; },
    };
  } },
});
vm.runInContext(fs.readFileSync(source, "utf8") + "\n" + fs.readFileSync(extension, "utf8"), context);

function request(operation, extra = {}, {key = secret, age = 0} = {}) {
  const action = "clg8514-v1";
  const ts = String(Math.floor(Date.now() / 1000) - age);
  const payload = Buffer.from(JSON.stringify({
    operation, doc_id: docId, student_name: "QA-SYNTHETIC",
    counseling_date: "2026-11-22", subject: "\uacf5\uc2182", ...extra,
  })).toString("base64url");
  const signature = crypto.createHmac("sha256", key).update([action, ts, payload].join("\n")).digest("hex");
  return context.doPost({ postData: { contents: JSON.stringify({action, ts, payload, signature}) }});
}

if (process.argv[3] === "--qa-plan") {
  const plan = JSON.parse(Buffer.from(process.argv[4], "base64").toString("utf8"));
  notes.text = plan.existing;
  const baseline = request("read");
  const result = request("append", {base_revision: baseline.revision, addition: plan.incoming});
  assert.equal(result.ok, true);
  const readback = request("read");
  assert.equal(readback.notes.slice(0, plan.existing.length), plan.existing);
  process.stdout.write(JSON.stringify({appends: notes.appends, suffix: readback.notes.slice(plan.existing.length)}));
  process.exit(0);
}

assert.equal(request("read", {}, {key: "b".repeat(64)}).code, "denied");
assert.equal(request("read", {}, {age: 300}).code, "denied");
assert.equal(request("read", {doc_id: "unknown-doc-00001"}).code, "denied");
assert.equal(opened, 0);
const baseline = request("read");
assert.equal(baseline.notes, existing);
assert.equal(saved, 0);
const proposed = "- QA-KEEP homework\n- QA-ADD";
const first = request("append", {base_revision: baseline.revision, addition: proposed});
assert.equal(first.written, true);
const afterFirst = request("read");
assert.equal(afterFirst.notes, existing + "\n- QA-ADD");
assert.equal(request("append", {base_revision: afterFirst.revision, addition: proposed}).written, false);
const afterSecond = request("read");
assert.equal(afterSecond.notes, afterFirst.notes);
assert.equal(notes.appends, 1);
assert.equal(saved, 1);

const concurrent = request("append", {base_revision: baseline.revision, addition: "- should not write"});
assert.equal(concurrent.code, "conflict");
assert.equal(notes.appends, 1);
docTitle = "Other QA document";
assert.equal(request("read").code, "target_mismatch");
docTitle = "CLG QA-SYNTHETIC test document";
label.text = "11/22\n\uacfc\ubaa9: \uacf5\uc2182";
assert.equal(request("read").code, "review");
assert.equal(notes.appends, 1);
label.text = rowDate;
table.push(row([cell("2026-11-23\n\uacfc\ubaa9: \uacf5\uc2182"), cell("another day")]));
const spare = [cell(""), cell("")];
table.push(row(spare));
const scope = {counseling_date: "2026-11-24"};
const fresh = request("read", scope);
assert.equal(fresh.notes, "");
assert.equal(request("append", {...scope, base_revision: fresh.revision, addition: "- QA-NEW"}).written, true);
assert.equal(request("read", scope).notes, "- QA-NEW");
assert.equal(notes.text, afterSecond.notes);
process.stdout.write("8514 append-only checks passed\n");
