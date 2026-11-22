/*
 * Counsel Note Google Apps Script Web App backup.
 *
 * This file is not executed by the local Streamlit app.
 * Keep it as a local recovery copy in case the deployed Apps Script project
 * is deleted or overwritten.
 *
 * Deployment expectation:
 * - Deploy as a Google Apps Script Web App.
 * - Execute as an account that can edit the target student Google Docs files.
 * - The local app sends: student_name, counseling_date, summary, doc_id, dedupe_key.
 */

const DEDUPE_KEY_RETENTION_DAYS = 90;
const DEDUPE_CLEANUP_INTERVAL_HOURS = 24;
const DEDUPE_CLEANUP_MAX_CHECK = 300;
const DEDUPE_CLEANUP_MARKER_KEY = "dedupe_cleanup:last_run";
const DOCUMENT_LEASE_PREFIX = "document_lock:";
const DOCUMENT_LEASE_WAIT_MS = 15000;
const DOCUMENT_LEASE_DURATION_MS = 120000;
const DOCUMENT_LEASE_GUARD_WAIT_MS = 1000;
const DOCUMENT_LEASE_POLL_MS = 100;
const SERVER_REVISION = "docs-speed-20261001";

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      throw new Error("POST 데이터가 없습니다.");
    }

    const data = JSON.parse(e.postData.contents);
    if (data.action === "clg8514-v1") {
      return clg8514AppendRequest_(data);
    }
    const docId = String(data.doc_id || "").trim();
    const studentName = String(data.student_name || "").trim();
    const counselingDate = String(data.counseling_date || "").trim();
    const summary = String(data.summary || "").trim();
    const dedupeKey = String(data.dedupe_key || "").trim();
    const verifyOnly = data.verify_only === true || String(data.action || "").trim() === "verify_existing";

    if (!docId) {
      throw new Error("doc_id가 없습니다.");
    }
    if (!summary) {
      throw new Error("summary가 없습니다.");
    }

    if (verifyOnly) {
      return verifyExistingCounselSummary_(docId, summary, counselingDate, dedupeKey);
    }

    const documentLease = acquireDocumentLease_(docId);

    try {
      const scriptProperties = PropertiesService.getScriptProperties();
      const cleanupResult = cleanupOldDedupeKeys_(scriptProperties);
      const propertyKey = buildDedupePropertyKey_(docId, dedupeKey);
      const dedupeKeyAlreadyRemembered = Boolean(propertyKey && scriptProperties.getProperty(propertyKey));

      const doc = DocumentApp.openById(docId);
      const body = doc.getBody();
      const tableLookup = {};
      const table = findCounselRecordTable_(body, tableLookup);

      const duplicate = findExistingCounselSummary_(table, summary, counselingDate, tableLookup.header);
      if (duplicate) {
        if (duplicate.action === "update_existing") {
          const existingDetailCell = table.getCell(duplicate.rowIndex, duplicate.colIndex);
          existingDetailCell.setText(summary);
          const updateReason = duplicate.duplicateReason || "same_date_replaced";
          rememberDedupeKey_(scriptProperties, propertyKey, studentName, counselingDate);
          doc.saveAndClose();
          return jsonOutput_({
            ok: true,
            message: duplicate.message || "updated_existing_same_date",
            student_name: studentName,
            counseling_date: counselingDate,
            dedupe_key: dedupeKey,
            duplicate_reason: updateReason,
            updated_existing: true,
            duplicate_skipped: false,
            dedupe_key_stale: false,
            row: duplicate.rowIndex,
            col: duplicate.colIndex,
            added_row: false,
            needs_review: false,
            review_category: "updated_existing_same_date",
            review_reason: "기존 같은 날짜 기록을 새 기록으로 교체했습니다.",
            dedupe_cleanup_checked: cleanupResult.checked,
            dedupe_cleanup_deleted: cleanupResult.deleted
          });
        }
        rememberDedupeKey_(scriptProperties, propertyKey, studentName, counselingDate);
        doc.saveAndClose();
        const duplicateReason = duplicate.duplicateReason || "content";
        const normalizedDuplicate = duplicateReason === "normalized_content";
        const unsafeSimilarRecord = duplicateReason === "unsafe_similar_record";
        return jsonOutput_({
          ok: true,
          duplicate_skipped: true,
          message: duplicate.message || "duplicate_skipped_by_content",
          student_name: studentName,
          counseling_date: counselingDate,
          dedupe_key: dedupeKey,
          duplicate_reason: duplicateReason,
          dedupe_key_stale: false,
          row: duplicate.rowIndex,
          col: duplicate.colIndex,
          needs_review: normalizedDuplicate || unsafeSimilarRecord,
          review_category: unsafeSimilarRecord
            ? "unsafe_similar_record"
            : normalizedDuplicate ? "already_saved_format_diff" : "",
          review_reason: unsafeSimilarRecord
            ? "문서 안에 같은 날짜의 유사 기록이 있으나 진도 외의 차이가 있어 자동 업데이트하지 않았습니다. 기존 기록과 새 기록을 확인하세요."
            : normalizedDuplicate
            ? "문서 안에 포맷만 다른 유사 기록이 있어 새로 추가하지 않았습니다."
            : "",
          dedupe_cleanup_checked: cleanupResult.checked,
          dedupe_cleanup_deleted: cleanupResult.deleted
        });
      }

      const target = findNextCounselRecordCell_(table, tableLookup.header);

      if (counselingDate) {
        const dateCell = table.getCell(target.rowIndex, target.dateColIndex);
        if (!dateCell.getText().trim()) {
          dateCell.setText(counselingDate);
        }
      }

      const detailCell = table.getCell(target.rowIndex, target.colIndex);
      detailCell.setText(summary);
      const styleReappliedAfterWrite = Boolean(
        target.styleTemplateCell && copyCounselEntryCellStyle_(target.styleTemplateCell, detailCell)
      );
      const responseReviewReasons = target.reviewReason ? target.reviewReason.split("; ") : [];
      if (dedupeKeyAlreadyRemembered) {
        addReviewReason_(
          responseReviewReasons,
          "dedupe_key는 있었지만 문서에서 동일 내용을 찾지 못해 새로 추가했습니다."
        );
      }
      if (target.styleTemplateCell && !styleReappliedAfterWrite) {
        addReviewReason_(responseReviewReasons, "본문 입력 후 상담 내용 칸 서식 재적용에 실패했습니다.");
      }
      const responseNeedsReview = target.needsReview || responseReviewReasons.length > 0;
      rememberDedupeKey_(scriptProperties, propertyKey, studentName, counselingDate);
      doc.saveAndClose();

      return jsonOutput_({
        ok: true,
        message: "added",
        student_name: studentName,
        counseling_date: counselingDate,
        dedupe_key: dedupeKey,
        dedupe_key_stale: dedupeKeyAlreadyRemembered,
        row: target.rowIndex,
        col: target.colIndex,
        added_row: target.addedRow,
        copied_row_template: target.copiedRowTemplate,
        row_style_reinforced: target.rowStyleReinforced,
        style_template_used: target.styleTemplateUsed,
        style_template_source: target.styleTemplateSource,
        style_reapplied_after_write: styleReappliedAfterWrite,
        max_cell_count: target.maxCellCount,
        needs_review: responseNeedsReview,
        review_reason: responseReviewReasons.join("; "),
        dedupe_cleanup_checked: cleanupResult.checked,
        dedupe_cleanup_deleted: cleanupResult.deleted
      });
    } finally {
      releaseDocumentLease_(documentLease);
    }
  } catch (err) {
    return jsonOutput_({
      ok: false,
      error: String(err)
    });
  }
}

function acquireDocumentLease_(docId) {
  const propertyKey = DOCUMENT_LEASE_PREFIX + String(docId || "").trim();
  const token = Utilities.getUuid();
  const deadline = Date.now() + DOCUMENT_LEASE_WAIT_MS;
  const scriptProperties = PropertiesService.getScriptProperties();

  while (Date.now() <= deadline) {
    const guard = LockService.getScriptLock();
    const remainingWait = Math.max(1, deadline - Date.now());
    const guardAcquired = guard.tryLock(
      Math.min(DOCUMENT_LEASE_GUARD_WAIT_MS, remainingWait)
    );
    if (guardAcquired) {
      try {
        const now = Date.now();
        const activeLease = parseDocumentLease_(scriptProperties.getProperty(propertyKey));
        if (!activeLease || activeLease.expiresAt <= now) {
          scriptProperties.setProperty(
            propertyKey,
            JSON.stringify({ token: token, expiresAt: now + DOCUMENT_LEASE_DURATION_MS })
          );
          return { propertyKey: propertyKey, token: token };
        }
      } finally {
        guard.releaseLock();
      }
    }

    const remainingSleep = deadline - Date.now();
    if (remainingSleep <= 0) {
      break;
    }
    Utilities.sleep(Math.min(DOCUMENT_LEASE_POLL_MS, remainingSleep));
  }

  throw new Error("document lock timeout: another request is updating this document");
}

function releaseDocumentLease_(lease) {
  if (!lease || !lease.propertyKey || !lease.token) {
    return;
  }
  const guard = LockService.getScriptLock();
  guard.waitLock(DOCUMENT_LEASE_WAIT_MS);
  try {
    const scriptProperties = PropertiesService.getScriptProperties();
    const activeLease = parseDocumentLease_(
      scriptProperties.getProperty(lease.propertyKey)
    );
    if (activeLease && activeLease.token === lease.token) {
      scriptProperties.deleteProperty(lease.propertyKey);
    }
  } finally {
    guard.releaseLock();
  }
}

function parseDocumentLease_(rawLease) {
  if (!rawLease) {
    return null;
  }
  try {
    const parsed = JSON.parse(rawLease);
    const token = String(parsed.token || "");
    const expiresAt = Number(parsed.expiresAt || 0);
    if (token && Number.isFinite(expiresAt)) {
      return { token: token, expiresAt: expiresAt };
    }
  } catch (err) {
    return null;
  }
  return null;
}

function verifyExistingCounselSummary_(docId, summary, counselingDate, dedupeKey) {
  try {
    const scriptProperties = PropertiesService.getScriptProperties();
    const propertyKey = buildDedupePropertyKey_(docId, dedupeKey);
    const dedupeKeyPresent = Boolean(propertyKey && scriptProperties.getProperty(propertyKey));
    const doc = DocumentApp.openById(docId);
    const body = doc.getBody();
    const tableLookup = {};
    const table = findCounselRecordTable_(body, tableLookup);
    const duplicate = findExistingCounselSummary_(table, summary, counselingDate, tableLookup.header);
    const foundExisting = Boolean(
      duplicate &&
      duplicate.duplicateReason !== "unsafe_similar_record" &&
      duplicate.action !== "update_existing"
    );
    return jsonOutput_({
      ok: true,
      verify_only: true,
      found_existing: foundExisting,
      duplicate_skipped: foundExisting,
      matched_by: foundExisting ? (duplicate.duplicateReason || "content") : "",
      counseling_date_matched: foundExisting ? true : false,
      doc_id_present: Boolean(docId),
      dedupe_key: dedupeKey,
      dedupe_key_present: dedupeKeyPresent,
      row: foundExisting ? duplicate.rowIndex : "",
      col: foundExisting ? duplicate.colIndex : "",
      message: foundExisting ? "verify_existing_found" : "verify_existing_not_found"
    });
  } catch (err) {
    return jsonOutput_({
      ok: false,
      verify_only: true,
      error_type: "verify_existing_error",
      message: String(err),
      doc_id_present: Boolean(docId)
    });
  }
}

function doGet() {
  return jsonOutput_({
    ok: true,
    message: "Counsel Note Apps Script is running.",
    server_revision: SERVER_REVISION
  });
}

function findCounselRecordTable_(body, tableLookup) {
  const tables = body.getTables();
  if (tables.length === 0) {
    throw new Error("문서에 표가 없습니다.");
  }

  if (tables.length === 1) {
    try {
      const header = findCounselHeader_(tables[0]);
      if (tableLookup) {
        tableLookup.header = header;
      }
      return tables[0];
    } catch (err) {
      // Keep the existing scored lookup and error behavior for malformed documents.
    }
  }

  const candidates = [];
  for (let i = 0; i < tables.length; i++) {
    const table = tables[i];
    const score = scoreCounselRecordTable_(table);
    if (score > 0) {
      candidates.push({ table, score, index: i });
    }
  }

  if (candidates.length === 0) {
    throw new Error("상담 기록 표를 찾지 못했습니다. 표에 '상담세부내용' 또는 '상담일자 / 상담자 / 세부내용' 헤더가 있는지 확인하세요.");
  }

  candidates.sort((a, b) => b.score - a.score || b.index - a.index);
  const selectedTable = candidates[0].table;
  if (tableLookup) {
    tableLookup.header = findCounselHeader_(selectedTable);
  }
  return selectedTable;
}

function scoreCounselRecordTable_(table) {
  let score = 0;
  const rowCount = table.getNumRows();

  for (let r = 0; r < rowCount; r++) {
    const row = table.getRow(r);
    const cellCount = row.getNumCells();

    for (let c = 0; c < cellCount; c++) {
      const text = normalizeText_(getCellText_(row, c));

      if (text.indexOf("상담세부내용") !== -1) {
        score += 10;
      }
      if (text.indexOf("상담일자") !== -1 && text.indexOf("상담자") !== -1 && text.indexOf("세부내용") !== -1) {
        score += 10;
      }
      if (text.indexOf("특이사항") !== -1 || text.indexOf("진도") !== -1 || text.indexOf("숙제") !== -1) {
        score += 1;
      }
    }
  }

  return score;
}

function findNextCounselRecordCell_(table, knownHeader) {
  const rowCount = table.getNumRows();
  if (rowCount === 0) {
    throw new Error("상담 기록 표에 행이 없습니다.");
  }

  const header = knownHeader || findCounselHeader_(table);
  const detailColIndex = header.detailColIndex;
  const dateColIndex = header.dateColIndex;
  const startRowIndex = header.headerRowIndex + 1;
  const maxCellCount = Math.max(getMaxTableCellCount_(table), dateColIndex + 1, detailColIndex + 1);
  const styleTemplate = findStyleTemplateCellForCounselEntry_(table, header);

  let lastFilledRow = -1;

  for (let r = startRowIndex; r < rowCount; r++) {
    const row = table.getRow(r);
    if (row.getNumCells() <= detailColIndex) {
      continue;
    }
    const text = getCellText_(row, detailColIndex);
    if (text) {
      lastFilledRow = r;
    }
  }

  const targetRowIndex = lastFilledRow === -1 ? startRowIndex : lastFilledRow + 1;
  const templateRowIndex = chooseCounselTemplateRowIndex_(
    table,
    startRowIndex,
    lastFilledRow,
    dateColIndex,
    detailColIndex
  );
  const ensureResult = ensureRowAndCells_(
    table,
    targetRowIndex,
    detailColIndex,
    templateRowIndex,
    styleTemplate,
    maxCellCount
  );

  return {
    rowIndex: targetRowIndex,
    colIndex: detailColIndex,
    dateColIndex: dateColIndex,
    addedRow: ensureResult.addedRow,
    copiedRowTemplate: ensureResult.copiedRowTemplate,
    rowStyleReinforced: ensureResult.rowStyleReinforced,
    styleTemplateUsed: ensureResult.styleTemplateUsed,
    styleTemplateCell: styleTemplate && styleTemplate.cell ? styleTemplate.cell : null,
    styleTemplateSource: ensureResult.styleTemplateSource,
    maxCellCount: ensureResult.maxCellCount,
    needsReview: ensureResult.needsReview,
    reviewReason: ensureResult.reviewReason
  };
}

function findExistingCounselSummary_(table, summary, counselingDate, knownHeader) {
  const expected = normalizeCounselSummaryForExactMatch_(summary);
  if (!expected) {
    return null;
  }
  const expectedDateKey = normalizeCounselDateForDuplicate_(counselingDate);
  if (!expectedDateKey) {
    return null;
  }
  let sameDateCandidate = null;

  const header = knownHeader || findCounselHeader_(table);
  const dateColIndex = header.dateColIndex;
  const detailColIndex = header.detailColIndex;
  const startRowIndex = header.headerRowIndex + 1;
  const rowCount = table.getNumRows();

  for (let r = startRowIndex; r < rowCount; r++) {
    const row = table.getRow(r);
    if (row.getNumCells() <= detailColIndex) {
      continue;
    }

    if (!rowMatchesCounselDate_(row, dateColIndex, expectedDateKey)) {
      continue;
    }

    const existing = normalizeCounselSummaryForExactMatch_(getCellText_(row, detailColIndex));
    if (existing && existing === expected) {
      return {
        rowIndex: r,
        colIndex: detailColIndex,
        duplicateReason: "content",
        message: "duplicate_skipped_by_content"
      };
    }
    sameDateCandidate = {
      rowIndex: r,
      colIndex: detailColIndex
    };
  }

  if (sameDateCandidate) {
    return {
      rowIndex: sameDateCandidate.rowIndex,
      colIndex: sameDateCandidate.colIndex,
      duplicateReason: "same_date_replaced",
      action: "update_existing",
      message: "updated_existing_same_date"
    };
  }

  return null;
}

function findCounselHeader_(table) {
  const rowCount = table.getNumRows();
  let fallbackHeader = null;

  for (let r = 0; r < rowCount; r++) {
    const row = table.getRow(r);
    const cellCount = row.getNumCells();

    for (let c = 0; c < cellCount; c++) {
      const text = normalizeText_(getCellText_(row, c));

      if (text.indexOf("상담일자") !== -1 && text.indexOf("상담자") !== -1 && text.indexOf("세부내용") !== -1) {
        return {
          headerRowIndex: r,
          dateColIndex: 0,
          detailColIndex: c
        };
      }
      if (!fallbackHeader && text.indexOf("상담세부내용") !== -1) {
        fallbackHeader = {
          headerRowIndex: r,
          dateColIndex: 0,
          detailColIndex: Math.min(c + 1, Math.max(cellCount - 1, 0))
        };
      }
    }
  }

  if (fallbackHeader) {
    return fallbackHeader;
  }

  throw new Error("상담 기록 표의 헤더 행을 찾지 못했습니다.");
}

function chooseCounselTemplateRowIndex_(table, startRowIndex, lastFilledRow, dateColIndex, detailColIndex) {
  for (let r = lastFilledRow; r >= startRowIndex; r--) {
    if (isTwoToneCounselRow_(table, r, dateColIndex, detailColIndex)) {
      return r;
    }
  }

  for (let r = startRowIndex; r < table.getNumRows(); r++) {
    if (isTwoToneCounselRow_(table, r, dateColIndex, detailColIndex)) {
      return r;
    }
  }

  if (lastFilledRow >= startRowIndex && table.getNumRows() > lastFilledRow) {
    return lastFilledRow;
  }

  if (table.getNumRows() > startRowIndex) {
    return startRowIndex;
  }

  return -1;
}

function isTwoToneCounselRow_(table, rowIndex, dateColIndex, detailColIndex) {
  const dateCell = getCellIfExists_(table, rowIndex, dateColIndex);
  const detailCell = getCellIfExists_(table, rowIndex, detailColIndex);
  return Boolean(dateCell && detailCell && isBlueCounselStyleCell_(dateCell) && !isBlueCounselStyleCell_(detailCell));
}

function ensureRowAndCells_(table, rowIndex, colIndex, templateRowIndex, styleTemplate, maxCellCount) {
  let addedRow = false;
  let copiedRowTemplate = true;
  const reviewReasons = [];
  const requiredCellCount = Math.max(Number(maxCellCount) || 0, colIndex + 1);
  const sourceTemplateRowIndex = templateRowIndex;

  while (table.getNumRows() <= rowIndex) {
    const result = appendCopiedCounselRow_(table, templateRowIndex, requiredCellCount);
    addedRow = true;
    copiedRowTemplate = copiedRowTemplate && result.copiedRowTemplate;
    if (!result.copiedRowTemplate) {
      addReviewReason_(reviewReasons, "행 템플릿 복사에 실패해 fallback 행을 사용했습니다.");
    }

    const appendedRowIndex = table.getNumRows() - 1;
    if (appendedRowIndex >= 0) {
      templateRowIndex = appendedRowIndex;
    }
  }

  const row = table.getRow(rowIndex);
  ensureRowHasCells_(row, requiredCellCount);
  const rowStyleReinforced = !addedRow || copyTableRowCellStyles_(
    table,
    sourceTemplateRowIndex,
    rowIndex,
    requiredCellCount,
    styleTemplate && styleTemplate.cell ? styleTemplate.cell : null
  );
  const targetCell = getCellIfExists_(table, rowIndex, colIndex);
  const styleTemplateSource = styleTemplate && styleTemplate.source ? styleTemplate.source : "";
  const styleTemplateUsed = Boolean(
    styleTemplate && styleTemplate.cell && targetCell && copyCounselEntryCellStyle_(styleTemplate.cell, targetCell)
  );
  if (addedRow) {
    addReviewReason_(reviewReasons, "새 행이 생성되었습니다. 위 행과 양식이 같은지 확인하세요.");
  }
  if (isFallbackCounselStyleSource_(styleTemplateSource)) {
    addReviewReason_(reviewReasons, "상담 내용 칸 서식을 찾지 못해 fallback 서식을 사용했습니다.");
  }
  if (!styleTemplateUsed) {
    addReviewReason_(reviewReasons, "상담 내용 칸 서식을 적용하지 못했습니다.");
  }
  if (addedRow && !rowStyleReinforced) {
    addReviewReason_(reviewReasons, "새 행의 전체 셀 서식 보강에 실패했습니다.");
  }

  return {
    addedRow: addedRow,
    copiedRowTemplate: copiedRowTemplate,
    rowStyleReinforced: rowStyleReinforced,
    styleTemplateUsed: styleTemplateUsed,
    styleTemplateSource: styleTemplateSource,
    maxCellCount: requiredCellCount,
    needsReview: reviewReasons.length > 0,
    reviewReason: reviewReasons.join("; ")
  };
}

function addReviewReason_(reviewReasons, reason) {
  if (reason && reviewReasons.indexOf(reason) === -1) {
    reviewReasons.push(reason);
  }
}

function isFallbackCounselStyleSource_(styleTemplateSource) {
  const source = String(styleTemplateSource || "").trim();
  return (
    source === "" ||
    source === "detail_blue_cell" ||
    source === "header_detail_blue_cell" ||
    source === "existing_detail_cell_fallback" ||
    source === "first_detail_cell" ||
    source === "last_cell"
  );
}

function appendCopiedCounselRow_(table, templateRowIndex, requiredCellCount) {
  if (templateRowIndex >= 0 && table.getNumRows() > templateRowIndex) {
    try {
      const templateRow = table.getRow(templateRowIndex);
      const copiedRow = templateRow.copy();
      clearTableRowText_(copiedRow);
      ensureRowHasCells_(copiedRow, requiredCellCount);
      table.appendTableRow(copiedRow);
      return {
        copiedRowTemplate: true
      };
    } catch (err) {
      // Fall back to a plain row if Apps Script cannot copy this table row.
    }
  }

  const newRow = table.appendTableRow();
  ensureRowHasCells_(newRow, requiredCellCount);
  clearTableRowText_(newRow);
  return {
    copiedRowTemplate: false
  };
}

function clearTableRowText_(row) {
  for (let i = 0; i < row.getNumCells(); i++) {
    try {
      row.getCell(i).setText("");
    } catch (err) {
      // Keep clearing other cells even if one cell cannot be edited.
    }
  }
}

function ensureRowHasCells_(row, requiredCellCount) {
  while (row.getNumCells() < requiredCellCount) {
    row.appendTableCell("");
  }
}

function copyTableRowCellStyles_(table, sourceRowIndex, targetRowIndex, requiredCellCount, fallbackCell) {
  if (sourceRowIndex < 0 || targetRowIndex < 0 || sourceRowIndex === targetRowIndex) {
    return false;
  }

  let copiedAny = false;
  for (let c = 0; c < requiredCellCount; c++) {
    const sourceCell = getCellIfExists_(table, sourceRowIndex, c) || fallbackCell;
    const targetCell = getCellIfExists_(table, targetRowIndex, c);
    if (!sourceCell || !targetCell) {
      continue;
    }
    try {
      copiedAny = copyCounselEntryCellStyle_(sourceCell, targetCell) || copiedAny;
    } catch (err) {
      // Keep copying remaining cells.
    }
  }
  return copiedAny;
}

function getMaxTableCellCount_(table) {
  let maxCellCount = 0;
  for (let r = 0; r < table.getNumRows(); r++) {
    try {
      maxCellCount = Math.max(maxCellCount, table.getRow(r).getNumCells());
    } catch (err) {
      // Keep scanning other rows.
    }
  }
  return maxCellCount;
}

function findStyleTemplateCellForCounselEntry_(table, header) {
  const detailColIndex = header.detailColIndex;
  const startRowIndex = header.headerRowIndex + 1;
  const rowCount = table.getNumRows();

  for (let r = rowCount - 1; r >= startRowIndex; r--) {
    const row = table.getRow(r);
    const cell = getCellIfExists_(table, r, detailColIndex);
    if (cell && getCellText_(row, detailColIndex) && !isBlueCounselStyleCell_(cell)) {
      return {
        cell: cell,
        source: "existing_white_detail_cell"
      };
    }
  }

  const firstDetailCell = getCellIfExists_(table, startRowIndex, detailColIndex);
  if (firstDetailCell && !isBlueCounselStyleCell_(firstDetailCell)) {
    return {
      cell: firstDetailCell,
      source: "first_white_detail_cell"
    };
  }

  for (let r = rowCount - 1; r >= startRowIndex; r--) {
    const row = table.getRow(r);
    const cell = getCellIfExists_(table, r, detailColIndex);
    if (cell && getCellText_(row, detailColIndex)) {
      return {
        cell: cell,
        source: isBlueCounselStyleCell_(cell) ? "existing_detail_cell_fallback" : "existing_detail_cell"
      };
    }
  }

  if (firstDetailCell) {
    return {
      cell: firstDetailCell,
      source: isBlueCounselStyleCell_(firstDetailCell) ? "first_detail_cell" : "first_white_detail_cell"
    };
  }

  for (let r = startRowIndex; r < rowCount; r++) {
    const cell = getCellIfExists_(table, r, detailColIndex);
    if (cell && isBlueCounselStyleCell_(cell)) {
      return {
        cell: cell,
        source: "detail_blue_cell"
      };
    }
  }

  const headerDetailCell = getCellIfExists_(table, header.headerRowIndex, detailColIndex);
  if (headerDetailCell) {
    return {
      cell: headerDetailCell,
      source: isBlueCounselStyleCell_(headerDetailCell) ? "header_detail_blue_cell" : "header_detail_cell"
    };
  }

  const lastCell = findLastCellInTable_(table);
  return {
    cell: lastCell,
    source: lastCell ? "last_cell" : ""
  };
}

function isBlueCounselStyleCell_(cell) {
  try {
    if (!cell || typeof cell.getBackgroundColor !== "function") {
      return false;
    }
    const color = String(cell.getBackgroundColor() || "").trim().toLowerCase();
    const match = color.match(/^#?([0-9a-f]{6})$/i);
    if (!match) {
      return false;
    }
    const hex = match[1];
    const red = parseInt(hex.substring(0, 2), 16);
    const green = parseInt(hex.substring(2, 4), 16);
    const blue = parseInt(hex.substring(4, 6), 16);
    return blue >= red + 20 && blue >= green;
  } catch (err) {
    return false;
  }
}

function copyCounselEntryCellStyle_(sourceCell, targetCell) {
  let copied = false;

  try {
    if (
      sourceCell &&
      targetCell &&
      typeof sourceCell.getBackgroundColor === "function" &&
      typeof targetCell.setBackgroundColor === "function"
    ) {
      const backgroundColor = sourceCell.getBackgroundColor();
      if (backgroundColor) {
        targetCell.setBackgroundColor(backgroundColor);
        copied = true;
      }
    }
  } catch (err) {
    // Best-effort style copy only.
  }

  try {
    if (
      sourceCell &&
      targetCell &&
      typeof sourceCell.getVerticalAlignment === "function" &&
      typeof targetCell.setVerticalAlignment === "function"
    ) {
      targetCell.setVerticalAlignment(sourceCell.getVerticalAlignment());
      copied = true;
    }
  } catch (err) {
    // Best-effort style copy only.
  }

  copyCellPadding_(sourceCell, targetCell);
  copyCellTextAlignment_(sourceCell, targetCell);
  copyCellTextAttributes_(sourceCell, targetCell);

  return copied;
}

function copyCellPadding_(sourceCell, targetCell) {
  const paddingPairs = [
    ["getPaddingTop", "setPaddingTop"],
    ["getPaddingBottom", "setPaddingBottom"],
    ["getPaddingLeft", "setPaddingLeft"],
    ["getPaddingRight", "setPaddingRight"]
  ];

  for (let i = 0; i < paddingPairs.length; i++) {
    try {
      const getter = paddingPairs[i][0];
      const setter = paddingPairs[i][1];
      if (
        sourceCell &&
        targetCell &&
        typeof sourceCell[getter] === "function" &&
        typeof targetCell[setter] === "function"
      ) {
        targetCell[setter](sourceCell[getter]());
      }
    } catch (err) {
      // Best-effort style copy only.
    }
  }
}

function copyCellTextAlignment_(sourceCell, targetCell) {
  try {
    if (!sourceCell || !targetCell || sourceCell.getNumChildren() === 0 || targetCell.getNumChildren() === 0) {
      return;
    }
    const sourceParagraph = sourceCell.getChild(0).asParagraph();
    const targetParagraph = targetCell.getChild(0).asParagraph();
    if (
      sourceParagraph &&
      targetParagraph &&
      typeof sourceParagraph.getAlignment === "function" &&
      typeof targetParagraph.setAlignment === "function"
    ) {
      targetParagraph.setAlignment(sourceParagraph.getAlignment());
    }
  } catch (err) {
    // Best-effort style copy only.
  }
}

function copyCellTextAttributes_(sourceCell, targetCell) {
  try {
    if (
      !sourceCell ||
      !targetCell ||
      typeof sourceCell.editAsText !== "function" ||
      typeof targetCell.editAsText !== "function"
    ) {
      return;
    }
    const sourceText = sourceCell.editAsText();
    const targetText = targetCell.editAsText();
    const sourceContent = sourceText.getText();
    if (!sourceContent) {
      return;
    }
    const attributes = sourceText.getAttributes(0);
    if (attributes && typeof targetText.setAttributes === "function") {
      targetText.setAttributes(attributes);
    }
  } catch (err) {
    // Best-effort style copy only.
  }
}

function getCellIfExists_(table, rowIndex, colIndex) {
  try {
    if (rowIndex < 0 || colIndex < 0 || table.getNumRows() <= rowIndex) {
      return null;
    }
    const row = table.getRow(rowIndex);
    if (row.getNumCells() <= colIndex) {
      return null;
    }
    return row.getCell(colIndex);
  } catch (err) {
    return null;
  }
}

function findLastCellInTable_(table) {
  for (let r = table.getNumRows() - 1; r >= 0; r--) {
    try {
      const row = table.getRow(r);
      for (let c = row.getNumCells() - 1; c >= 0; c--) {
        return row.getCell(c);
      }
    } catch (err) {
      // Keep scanning previous rows.
    }
  }
  return null;
}

function getCellText_(row, colIndex) {
  try {
    if (row.getNumCells() <= colIndex) {
      return "";
    }
    return String(row.getCell(colIndex).getText() || "").trim();
  } catch (err) {
    return "";
  }
}

function buildCounselDuplicateFingerprint_(summary) {
  const parsed = normalizeCounselSummaryForDuplicate_(summary);
  return buildCounselDuplicateFingerprintFromParsed_(parsed, true);
}

function buildCounselDuplicateFingerprintWithoutProgress_(summary) {
  const parsed = normalizeCounselSummaryForDuplicate_(summary);
  return buildCounselDuplicateFingerprintFromParsed_(parsed, false);
}

function buildCounselDuplicateFingerprintFromParsed_(parsed, includeProgress) {
  const parts = [];
  if (parsed.subjectBook) {
    parts.push("subject:" + parsed.subjectBook);
  }
  if (includeProgress && parsed.progress) {
    parts.push("progress:" + parsed.progress);
  }
  if (parsed.notes) {
    parts.push("notes:" + parsed.notes);
  }
  if (parsed.homework.length) {
    parts.push("homework:" + parsed.homework.join("|"));
  }
  return parts.join("\n");
}

function isCounselDuplicateFingerprintUsable_(fingerprint) {
  const text = String(fingerprint || "");
  return text.indexOf("progress:") !== -1 && (
    text.indexOf("homework:") !== -1 || text.indexOf("notes:") !== -1
  );
}

function normalizeCounselSummaryForDuplicate_(summary) {
  const lines = String(summary || "")
    .replace(/\r/g, "\n")
    .split("\n")
    .map(line => String(line || "").trim())
    .filter(line => line);
  const notes = [];
  const homework = {};
  let progress = "";
  let subjectBook = "";
  let homeworkFormat = "";
  let hasStandardProgress = false;
  let hasHomeworkHeader = false;
  let hasBulletHomework = false;
  let hasTableHomework = false;
  let inHomework = false;

  for (let i = 0; i < lines.length; i++) {
    const originalLine = lines[i];
    const line = stripCounselBullet_(originalLine);
    if (!line) {
      continue;
    }

    const progressMatch = line.match(/^진도\s*(?:\(([^)]*)\))?\s*[:：]\s*(.+)$/i);
    if (progressMatch) {
      subjectBook = normalizeCounselDuplicateText_(progressMatch[1] || "");
      const progressBody = progressMatch[2] || "";
      progress = normalizeCounselDuplicateText_(subjectBook + "|" + progressBody);
      hasStandardProgress = /^진도\([^)]{1,80}\)\s*[:：]\s*\S/.test(line);
      inHomework = false;
      continue;
    }

    if (/^숙제\s*[:：]?$/.test(line)) {
      hasHomeworkHeader = true;
      inHomework = true;
      continue;
    }

    if (isCounselHomeworkTableHeader_(line)) {
      hasTableHomework = true;
      homeworkFormat = mergeCounselHomeworkFormat_(homeworkFormat, "table");
      inHomework = true;
      continue;
    }

    if (inHomework || looksLikeCounselHomeworkLine_(line)) {
      const homeworkItem = parseCounselHomeworkLine_(line);
      if (homeworkItem && homeworkItem.book) {
        if (homeworkItem.format === "table") {
          hasTableHomework = true;
        }
        if (homeworkItem.format === "bullet" && /^[\-\u2022\u00b7]\s*/.test(originalLine)) {
          hasBulletHomework = true;
        }
        homeworkFormat = mergeCounselHomeworkFormat_(homeworkFormat, homeworkItem.format || "");
        const key = normalizeCounselDuplicateText_(homeworkItem.book);
        if (!homework[key]) {
          homework[key] = {
            book: key,
            progress: "",
            next: "",
            text: ""
          };
        }
        if (homeworkItem.progress) {
          homework[key].progress = normalizeCounselDuplicateText_(homeworkItem.progress);
        }
        if (homeworkItem.next) {
          homework[key].next = normalizeCounselDuplicateText_(homeworkItem.next);
        }
        if (homeworkItem.text) {
          homework[key].text = normalizeCounselDuplicateText_(homeworkItem.text);
        }
        continue;
      }
    }

    const noteText = extractCounselDuplicateNoteText_(line);
    if (noteText) {
      notes.push(noteText);
    }
    inHomework = false;
  }

  const homeworkKeys = Object.keys(homework).sort();
  const homeworkParts = homeworkKeys.map(key => {
    const item = homework[key];
    return [
      item.book,
      item.progress ? "progress" + item.progress : "",
      item.next ? "next" + item.next : "",
      item.text ? "text" + item.text : ""
    ].filter(Boolean).join(":");
  });

  return {
    subjectBook: subjectBook,
    progress: progress,
    notes: normalizeCounselDuplicateText_(notes.join(" ")),
    homework: homeworkParts,
    homeworkFormat: homeworkFormat,
    summaryFormat: hasStandardProgress && hasHomeworkHeader && hasBulletHomework && !hasTableHomework
      ? "standard"
      : hasTableHomework ? "table" : "other"
  };
}

function normalizeCounselDateForDuplicate_(value) {
  const digits = String(value || "").replace(/[^0-9]/g, "");
  return digits.length >= 8 ? digits.slice(0, 8) : "";
}

function rowMatchesCounselDate_(row, dateColIndex, expectedDateKey) {
  if (!expectedDateKey || dateColIndex < 0 || row.getNumCells() <= dateColIndex) {
    return false;
  }
  const existingDateKey = normalizeCounselDateForDuplicate_(getCellText_(row, dateColIndex));
  return Boolean(existingDateKey && existingDateKey === expectedDateKey);
}

function classifyCounselFormatOnlyUpdate_(existingParsed, expectedParsed) {
  if (!expectedParsed || expectedParsed.summaryFormat !== "standard") {
    return { formatOnlyUpdate: false };
  }
  if (!existingParsed || !existingParsed.progress || !expectedParsed.progress) {
    return { formatOnlyUpdate: false };
  }
  if (!existingParsed.subjectBook || !expectedParsed.subjectBook || existingParsed.subjectBook !== expectedParsed.subjectBook) {
    return { formatOnlyUpdate: false };
  }
  if (existingParsed.progress !== expectedParsed.progress) {
    return { formatOnlyUpdate: false };
  }
  if (!existingParsed.homework.length || !expectedParsed.homework.length) {
    return { formatOnlyUpdate: false };
  }
  const existingFingerprint = buildCounselDuplicateFingerprintFromParsed_(existingParsed, true);
  const expectedFingerprint = buildCounselDuplicateFingerprintFromParsed_(expectedParsed, true);
  if (!existingFingerprint || existingFingerprint !== expectedFingerprint) {
    return { formatOnlyUpdate: false };
  }
  return { formatOnlyUpdate: true };
}

function classifyCounselProgressOnlyUpdate_(existingParsed, expectedParsed) {
  const existingRest = buildCounselDuplicateFingerprintFromParsed_(existingParsed, false);
  const expectedRest = buildCounselDuplicateFingerprintFromParsed_(expectedParsed, false);
  const restSame = Boolean(existingRest && expectedRest && existingRest === expectedRest);
  const sameSubject = Boolean(
    existingParsed.subjectBook &&
    expectedParsed.subjectBook &&
    existingParsed.subjectBook === expectedParsed.subjectBook
  );
  if (!restSame || !sameSubject) {
    return { progressOnlyUpdate: false, unsafeSimilar: false };
  }

  if (!existingParsed.progress || !expectedParsed.progress) {
    return { progressOnlyUpdate: false, unsafeSimilar: true };
  }

  if (existingParsed.progress === expectedParsed.progress) {
    return { progressOnlyUpdate: false, unsafeSimilar: false };
  }

  if (!counselHomeworkFormatsCompatible_(existingParsed.homeworkFormat, expectedParsed.homeworkFormat)) {
    return { progressOnlyUpdate: false, unsafeSimilar: true };
  }

  return { progressOnlyUpdate: true, unsafeSimilar: false };
}

function counselHomeworkFormatsCompatible_(aFormat, bFormat) {
  const a = String(aFormat || "");
  const b = String(bFormat || "");
  if (!a || !b) {
    return false;
  }
  if (a === "mixed" || b === "mixed") {
    return false;
  }
  return a === b;
}

function mergeCounselHomeworkFormat_(currentFormat, nextFormat) {
  const current = String(currentFormat || "");
  const next = String(nextFormat || "");
  if (!next) {
    return current;
  }
  if (!current) {
    return next;
  }
  return current === next ? current : "mixed";
}

function stripCounselBullet_(line) {
  return String(line || "").replace(/^[\-\u2022\u00b7]\s*/, "").trim();
}

function normalizeCounselDuplicateText_(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[：]/g, ":")
    .replace(/[（）]/g, match => match === "（" ? "(" : ")")
    .replace(/다음\s*숙제/g, "다음숙제")
    .replace(/이번\s*숙제/g, "이번숙제")
    .replace(/진행\s*률/g, "진행률")
    .replace(/\s+/g, "")
    .replace(/[,\.\-_\[\]\(\):：\/|]+/g, "");
}

function isCounselHomeworkTableHeader_(line) {
  const normalized = normalizeCounselDuplicateText_(line);
  return normalized.indexOf("교재") !== -1 &&
    normalized.indexOf("진행률") !== -1 &&
    normalized.indexOf("다음숙제") !== -1;
}

function looksLikeCounselHomeworkLine_(line) {
  if (line.indexOf("/") !== -1) {
    return true;
  }
  return /^[^:：]{1,30}[:：]\s*(진행률|다음\s*숙제|이번\s*숙제|숙제)/.test(line);
}

function parseCounselHomeworkLine_(line) {
  const cleanLine = stripCounselBullet_(line);
  if (!cleanLine || isCounselHomeworkTableHeader_(cleanLine)) {
    return null;
  }

  if (cleanLine.indexOf("/") !== -1) {
    const parts = cleanLine.split("/").map(part => part.trim());
    const book = parts[0] || "";
    if (!book || normalizeCounselDuplicateText_(book) === "교재") {
      return null;
    }
    const progress = parts.length >= 5 ? parts[2] : (/%/.test(parts[2] || "") ? parts[2] : "");
    const next = parts.length >= 5 ? parts[4] : (parts[3] || "");
    return {
      book: book,
      progress: progress || "",
      next: next || "",
      text: "",
      format: "table"
    };
  }

  const colonMatch = cleanLine.match(/^([^:：]+)[:：]\s*(.+)$/);
  if (!colonMatch) {
    return null;
  }
  const book = colonMatch[1].trim();
  const rest = colonMatch[2].trim();
  if (!book || /^(진도|특이사항|상담\s*참고사항|오늘\s*확인할\s*것)$/i.test(book)) {
    return null;
  }

  const progressMatch = rest.match(/진행률\s*([0-9]+%)/);
  const nextMatch = rest.match(/다음\s*숙제\s*([^,，]+)/);
  const text = rest
    .replace(/진행률\s*[0-9]+%/g, "")
    .replace(/다음\s*숙제\s*/g, "")
    .replace(/[,，]/g, " ")
    .trim();
  return {
    book: book,
    progress: progressMatch ? progressMatch[1] : "",
    next: nextMatch ? nextMatch[1].trim() : text,
    text: "",
    format: "bullet"
  };
}

function extractCounselDuplicateNoteText_(line) {
  const cleanLine = stripCounselBullet_(line);
  if (!cleanLine) {
    return "";
  }
  if (/^\[.*\]$/.test(cleanLine)) {
    return "";
  }
  if (/^(상담\s*참고사항|오늘\s*확인할\s*것|수업\s*전\s*확인|숙제\s*체크)$/i.test(cleanLine)) {
    return "";
  }
  const noteMatch = cleanLine.match(/^(특이사항|상담\s*참고사항)\s*[:：]\s*(.+)$/i);
  if (noteMatch) {
    return normalizeCounselDuplicateText_(noteMatch[2]);
  }
  return normalizeCounselDuplicateText_(cleanLine);
}

function normalizeText_(value) {
  return String(value || "").replace(/\s+/g, "");
}

function normalizeCounselSummaryForExactMatch_(value) {
  return String(value || "").replace(/\r\n?/g, "\n").trim();
}

function buildDedupePropertyKey_(docId, dedupeKey) {
  const cleanDocId = String(docId || "").trim();
  const cleanDedupeKey = String(dedupeKey || "").trim();
  if (!cleanDocId || !cleanDedupeKey) {
    return "";
  }
  return "dedupe:" + cleanDocId + ":" + cleanDedupeKey;
}

function rememberDedupeKey_(scriptProperties, propertyKey, studentName, counselingDate) {
  if (!scriptProperties || !propertyKey) {
    return;
  }

  scriptProperties.setProperty(propertyKey, JSON.stringify({
    student_name: String(studentName || ""),
    counseling_date: String(counselingDate || ""),
    savedAt: new Date().toISOString(),
    saved_at: new Date().toISOString()
  }));
}

function cleanupOldDedupeKeys_(scriptProperties) {
  const result = {
    checked: 0,
    deleted: 0
  };
  try {
    if (!scriptProperties || !shouldRunDedupeCleanup_(scriptProperties)) {
      return result;
    }
    const now = new Date();
    const cutoffTime = now.getTime() - (DEDUPE_KEY_RETENTION_DAYS * 24 * 60 * 60 * 1000);
    const properties = scriptProperties.getProperties();
    const keys = Object.keys(properties);

    for (let i = 0; i < keys.length && result.checked < DEDUPE_CLEANUP_MAX_CHECK; i++) {
      const key = keys[i];
      if (!isDedupePropertyKey_(key)) {
        continue;
      }
      result.checked += 1;
      const savedAtTime = parseDedupeKeyTimestamp_(properties[key]);
      if (savedAtTime === null) {
        continue;
      }
      if (savedAtTime < cutoffTime) {
        scriptProperties.deleteProperty(key);
        result.deleted += 1;
      }
    }
    scriptProperties.setProperty(DEDUPE_CLEANUP_MARKER_KEY, now.toISOString());
  } catch (err) {
    return result;
  }
  return result;
}

function shouldRunDedupeCleanup_(scriptProperties) {
  try {
    const lastRunText = scriptProperties.getProperty(DEDUPE_CLEANUP_MARKER_KEY);
    const lastRunTime = parseIsoDateTime_(lastRunText);
    if (lastRunTime === null) {
      return true;
    }
    const elapsedMs = new Date().getTime() - lastRunTime;
    return elapsedMs >= (DEDUPE_CLEANUP_INTERVAL_HOURS * 60 * 60 * 1000);
  } catch (err) {
    return false;
  }
}

function isDedupePropertyKey_(key) {
  return String(key || "").indexOf("dedupe:") === 0;
}

function parseDedupeKeyTimestamp_(propertyValue) {
  const text = String(propertyValue || "").trim();
  if (!text) {
    return null;
  }
  try {
    const parsed = JSON.parse(text);
    const savedAt = parsed.savedAt || parsed.saved_at || parsed.saved_at_iso || "";
    return parseIsoDateTime_(savedAt);
  } catch (err) {
    return null;
  }
}

function parseIsoDateTime_(value) {
  const text = String(value || "").trim();
  if (!text) {
    return null;
  }
  const time = new Date(text).getTime();
  return isNaN(time) ? null : time;
}

function jsonOutput_(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
