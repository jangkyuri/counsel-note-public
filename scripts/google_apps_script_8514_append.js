// 8514 v1 is opt-in. Its signed envelope is never handled by legacy save.
function clg8514AppendRequest_(envelope) {
  try {
    const properties = PropertiesService.getScriptProperties();
    const secret = properties.getProperty("CLG8514_APPEND_SECRET");
    const packet = clg8514VerifyEnvelope_(envelope, secret);
    const allowed = JSON.parse(properties.getProperty("CLG8514_APPEND_ALLOWED_DOC_IDS") || "[]");
    if (!packet || !Array.isArray(allowed) || !allowed.includes(packet.doc_id)) {
      return clg8514Failure_("denied");
    }
    if (!clg8514ValidPacket_(packet)) {
      return clg8514Failure_("invalid_request");
    }
    const lease = acquireDocumentLease_(packet.doc_id);
    try {
      const doc = DocumentApp.openById(packet.doc_id);
      if (!clg8514MatchesTitle_(doc.getName(), packet.student_name)) {
        return clg8514Failure_("target_mismatch");
      }
      const target = clg8514FindTarget_(doc, packet);
      if (!target) return clg8514Failure_("review");
      const notes = target.noteCell.getText();
      const revision = clg8514Hex_(Utilities.computeDigest(
        Utilities.DigestAlgorithm.SHA_256,
        JSON.stringify([packet.doc_id, packet.counseling_date, packet.subject, target.row, target.label, notes]),
        Utilities.Charset.UTF_8
      ));
      if (packet.operation === "read") {
        return jsonOutput_({ok: true, protocol: "clg8514-v1", notes: notes, revision: revision});
      }
      if (packet.base_revision !== revision) return clg8514Failure_("conflict");
      const addition = clg8514MissingLines_(notes, packet.addition);
      if (!addition.length) {
        return jsonOutput_({ok: true, protocol: "clg8514-v1", written: false});
      }
      if (!target.label) {
        target.dateCell.appendParagraph(packet.counseling_date);
        target.dateCell.appendParagraph("\uacfc\ubaa9: " + packet.subject);
      }
      addition.forEach((line) => target.noteCell.appendParagraph(line));
      doc.saveAndClose();
      return jsonOutput_({ok: true, protocol: "clg8514-v1", written: true});
    } finally {
      releaseDocumentLease_(lease);
    }
  } catch (_) {
    return clg8514Failure_("unavailable");
  }
}

function clg8514Failure_(code) {
  return jsonOutput_({ok: false, protocol: "clg8514-v1", code: code});
}

function clg8514Hex_(bytes) {
  return bytes.map((byte) => ("0" + ((byte & 255).toString(16))).slice(-2)).join("");
}

function clg8514VerifyEnvelope_(envelope, secret) {
  if (!/^[a-f0-9]{64}$/.test(secret || "") ||
      typeof envelope.ts !== "string" || !/^\d{10}$/.test(envelope.ts) ||
      Math.abs(Math.floor(Date.now() / 1000) - Number(envelope.ts)) > 120 ||
      typeof envelope.payload !== "string" || envelope.payload.length > 160000 ||
      !/^[A-Za-z0-9_-]+={0,2}$/.test(envelope.payload) ||
      typeof envelope.signature !== "string" || !/^[a-f0-9]{64}$/.test(envelope.signature)) return null;
  const expected = clg8514Hex_(Utilities.computeHmacSha256Signature(
    ["clg8514-v1", envelope.ts, envelope.payload].join("\n"), secret, Utilities.Charset.UTF_8
  ));
  let difference = 0;
  for (let i = 0; i < expected.length; i++) {
    difference |= expected.charCodeAt(i) ^ envelope.signature.charCodeAt(i);
  }
  if (difference !== 0) return null;
  try {
    const value = JSON.parse(Utilities.newBlob(
      Utilities.base64DecodeWebSafe(envelope.payload)
    ).getDataAsString("UTF-8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (_) {
    return null;
  }
}

function clg8514Subject_(value) {
  if (typeof value !== "string") return "";
  const key = value.normalize("NFKC").replace(/\s+/g, "").toLowerCase().replace(/(?:apex|a:ble|able|\uae30\ubcf8)$/, "");
  const aliases = {
    "\uacf5\ud1b5\uc218\ud5591": "\uacf5\uc2181", "\uacf5\uc2181": "\uacf5\uc2181",
    "\uacf5\ud1b5\uc218\ud5592": "\uacf5\uc2182", "\uacf5\uc2182": "\uacf5\uc2182",
    "\ubbf8\uc801\ubd841": "\ubbf8\uc8011", "\ubbf8\uc8011": "\ubbf8\uc8011",
    "\ubbf8\uc801\ubd842": "\ubbf8\uc8012", "\ubbf8\uc8012": "\ubbf8\uc8012",
    "\ud655\ub960\uacfc\ud1b5\uacc4": "\ud655\ud1b5", "\ud655\ud1b5": "\ud655\ud1b5",
    "\uae30\ud558": "\uae30\ud558", "\ub300\uc218": "\ub300\uc218",
  };
  return Object.prototype.hasOwnProperty.call(aliases, key) ? aliases[key] : "";
}

function clg8514ValidPacket_(packet) {
  if (typeof packet.doc_id !== "string" || !/^[A-Za-z0-9_-]{15,}$/.test(packet.doc_id) ||
      typeof packet.student_name !== "string" || !packet.student_name.trim() || packet.student_name.length > 120 ||
      typeof packet.counseling_date !== "string" || !/^20\d{2}-\d{2}-\d{2}$/.test(packet.counseling_date) ||
      !packet.subject || clg8514Subject_(packet.subject) !== packet.subject) return false;
  const day = new Date(packet.counseling_date + "T00:00:00Z");
  if (isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== packet.counseling_date) return false;
  if (packet.operation === "read") return true;
  return packet.operation === "append" && typeof packet.base_revision === "string" &&
    /^[a-f0-9]{64}$/.test(packet.base_revision) &&
    typeof packet.addition === "string" && !!packet.addition.trim() && packet.addition.length <= 30000;
}

function clg8514MatchesTitle_(title, student) {
  const tokens = (value) => value.normalize("NFC").toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  const wanted = tokens(student);
  const parts = tokens(title);
  return wanted.length > 0 && parts.some((_, i) => wanted.every((word, j) => word === parts[i + j]));
}

function clg8514Header_(row) {
  const values = [];
  for (let i = 0; i < row.getNumCells(); i++) values.push(row.getCell(i).getText().replace(/\s+/g, ""));
  const exact = ["\uc0c1\ub2f4\uc77c\uc790", "\uc0c1\ub2f4\uc77c"].includes(values[0]);
  const details = values.map((value, i) => ({ value: value, i: i })).filter((cell) =>
    cell.i > 0 && (cell.value === "\uc138\ubd80\ub0b4\uc6a9" || cell.value === "\uc0c1\ub2f4\uc138\ubd80\ub0b4\uc6a9")
  );
  if (exact && details.length === 1) return details[0].i;
  const combined = values.findIndex((value) =>
    ["\uc0c1\ub2f4\uc77c\uc790", "\uc0c1\ub2f4\uc790", "\uc138\ubd80\ub0b4\uc6a9"].every((part) => value.includes(part))
  );
  if (combined > 0) return combined;
  const old = values.findIndex((value) => value === "\uc0c1\ub2f4\uc138\ubd80\ub0b4\uc6a9");
  return old >= 0 && values.length > 1 ? Math.min(old + 1, values.length - 1) : null;
}

function clg8514FindTarget_(doc, packet) {
  const tabs = doc.getTabs();
  if (tabs.length !== 1 || tabs[0].getChildTabs().length) return null;
  const candidates = [];
  doc.getBody().getTables().forEach((table) => {
    for (let i = 0; i < table.getNumRows(); i++) {
      const detail = clg8514Header_(table.getRow(i));
      if (detail !== null) candidates.push({ table: table, index: i, detail: detail });
    }
  });
  if (candidates.length !== 1) return null;
  const {table, index, detail} = candidates[0];
  const [year, month, day] = packet.counseling_date.split("-").map(Number);
  const matches = [];
  let blank = null;
  for (let i = index + 1; i < table.getNumRows(); i++) {
    const row = table.getRow(i);
    if (row.getNumCells() <= detail) {
      blank = null;
      continue;
    }
    const dateCell = row.getCell(0);
    const noteCell = row.getCell(detail);
    const label = dateCell.getText();
    const target = {row: i, dateCell: dateCell, noteCell: noteCell, label: label};
    const isEmpty = Array.from({length: row.getNumCells()}, (_, j) => row.getCell(j).getText().trim())
      .every((value) => !value);
    if (isEmpty) {
      if (blank === null) blank = target;
      continue;
    }
    blank = null;
    const date = label.trim().match(/^(?:(20\d{2})\s*[-./\ub144]\s*)?(\d{1,2})\s*[-./\uc6d4]\s*(\d{1,2})(?!\d)/);
    if (!date || Number(date[2]) !== month || Number(date[3]) !== day) continue;
    if (!date[1]) return null;
    if (Number(date[1]) !== year) continue;
    const subjects = Array.from(label.matchAll(/^\s*\uacfc\ubaa9\s*[:\uff1a]\s*(.+?)\s*$/gm), (entry) => clg8514Subject_(entry[1]));
    if (subjects.length !== 1 || !subjects[0]) return null;
    if (subjects[0] === packet.subject) matches.push(target);
  }
  return matches.length === 1 ? matches[0] : matches.length === 0 ? blank : null;
}

function clg8514MissingLines_(existing, incoming) {
  const key = (line) => line.normalize("NFC").trim().replace(/^[-*\u2022]\s+/, "").replace(/\s+/g, " ").toLowerCase();
  const known = new Set(existing.split(/\r?\n/).map(key));
  return incoming.split(/\r?\n/).map((line) => line.trim()).filter((line) => {
    const token = key(line);
    if (!token || known.has(token)) return false;
    known.add(token);
    return true;
  });
}
