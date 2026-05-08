/**
 * Google Apps Script — receives comments from the Weekly viewer (Евгений)
 * and writes them to a sheet named "Weekly Feedback" in the same spreadsheet.
 *
 * Deployment:
 * 1. Open the spreadsheet → Extensions → Apps Script
 * 2. Replace the default Code.gs content with this file
 * 3. Deploy → New deployment → Web app
 *    · Execute as: Me
 *    · Who has access: Anyone
 * 4. Copy the deployment URL → paste into SHEETS_CONFIG.appsScriptUrl in data.jsx
 * 5. Push to GitHub
 */

const FEEDBACK_SHEET_NAME = "Weekly Feedback";

const HEADERS = [
  "ID", "Timestamp", "Week", "Source Sheet",
  "Anchor", "Label", "Comment", "Author", "Status",
];

function getOrCreateSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(FEEDBACK_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(FEEDBACK_SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length)
      .setFontWeight("bold")
      .setBackground("#f0f0f0");
  }
  return sheet;
}

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const sheet = getOrCreateSheet();
    sheet.appendRow([
      data.id || "",
      data.ts || new Date().toISOString(),
      data.weekLabel || "",
      data.sourceSheet || "",
      data.anchor || "",
      data.label || "",
      data.text || "",
      data.author || "Евгений",
      data.status || "open",
    ]);
    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Simple GET handler for health-check
function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, sheet: FEEDBACK_SHEET_NAME }))
    .setMimeType(ContentService.MimeType.JSON);
}
