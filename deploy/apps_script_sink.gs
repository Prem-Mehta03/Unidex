/**
 * Unidex event receiver: appends reports and search logs to this Google Sheet.
 *
 * Setup (see docs/deploy.md): open a new Google Sheet > Extensions > Apps Script, paste this
 * whole file, then Project Settings > Script properties > add SECRET = a long random string
 * (the same value you give the website as EVENT_WEBHOOK_SECRET). Deploy > New deployment >
 * Web app > Execute as: Me, Who has access: Anyone. Copy the web app URL.
 *
 * Each kind of event ("search", "report") gets its own tab; columns are created from the
 * event's fields the first time they appear.
 */

function doPost(e) {
  var body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return reply_('bad request');
  }
  var secret = PropertiesService.getScriptProperties().getProperty('SECRET');
  if (!secret || body.secret !== secret) {
    return reply_('forbidden');
  }
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var book = SpreadsheetApp.getActiveSpreadsheet();
    (body.events || []).forEach(function (event) {
      appendEvent_(book, event);
    });
  } finally {
    lock.releaseLock();
  }
  return reply_('ok');
}

function appendEvent_(book, event) {
  var name = String(event.kind || 'other').replace(/[^a-z_]/gi, '').slice(0, 30) || 'other';
  var sheet = book.getSheetByName(name) || book.insertSheet(name);
  var data = event.data || {};
  var header = sheet.getLastRow() === 0 ? [] : sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  if (header.length === 0) {
    header = ['time'];
  }
  Object.keys(data).forEach(function (key) {
    if (header.indexOf(key) === -1) {
      header.push(key);
    }
  });
  sheet.getRange(1, 1, 1, header.length).setValues([header]);
  var row = header.map(function (column) {
    return column === 'time' ? event.at : safe_(data[column]);
  });
  sheet.appendRow(row);
}

// A text starting with = + - @ would be read as a formula; a leading quote keeps it text.
function safe_(value) {
  if (value === undefined || value === null) {
    return '';
  }
  if (typeof value === 'string' && /^[=+\-@]/.test(value)) {
    return "'" + value;
  }
  return value;
}

function reply_(text) {
  return ContentService.createTextOutput(text);
}
