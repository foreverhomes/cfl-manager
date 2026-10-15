/**
 * CFL Manager publisher. Lets the app rewrite one tab of this spreadsheet.
 *
 * Setup (once):
 *   1. In the sheet: Extensions > Apps Script. Replace the editor's contents with this file
 *      (use the copy `python -m cfl publish --script` writes, which has your token filled in).
 *   2. Deploy > New deployment > type "Web app". Execute as: Me. Who has access: Anyone.
 *      Authorize when asked, then copy the web app URL.
 *   3. Put the URL in the app's .env as CFL_SHEET_PUBLISH_URL.
 * After changing this script, use Deploy > Manage deployments > edit > Version: New version.
 */
const PUBLISH_TOKEN = "__PUBLISH_TOKEN__";

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  if (!PUBLISH_TOKEN || PUBLISH_TOKEN.indexOf("__") === 0 || body.token !== PUBLISH_TOKEN) {
    return reply({ ok: false, error: "bad token" });
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(body.tab) || ss.insertSheet(body.tab);
    const width = body.width;
    const rows = body.values.map(function (r) {
      const out = r.slice(0, width);
      while (out.length < width) out.push("");
      return out;
    });
    if (sheet.getMaxRows() < rows.length) sheet.insertRowsAfter(sheet.getMaxRows(), rows.length - sheet.getMaxRows());
    // Clear everything the app manages (old content can be longer than the new), then write.
    const clearRows = Math.max(rows.length, sheet.getLastRow(), 1);
    sheet.getRange(1, 1, clearRows, width).clearContent().clearFormat();
    sheet.getRange(1, 1, rows.length, width).setValues(rows);
    (body.styles || []).forEach(function (s) {
      const range = sheet.getRange(s.row, 1, 1, width);
      if (s.bold) range.setFontWeight("bold");
      if (s.background) range.setBackground(s.background);
    });
    if (body.frozen_rows != null) sheet.setFrozenRows(body.frozen_rows);
    sheet.autoResizeColumn(1);
    return reply({ ok: true, rows: rows.length });
  } finally {
    lock.releaseLock();
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
