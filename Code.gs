/**
 * 팀준 운영진 질문 — 저장 창구 (Google Apps Script)
 *
 * 하는 일: 휴대폰 질문 페이지(GitHub Pages)가 이 창구로
 *   ① 질문과 지금 답을 읽고 ② 답을 저장하고 ③ '작성 중' 표시를 남긴다.
 * 답은 시트의 '답' 칸(C)에 바로 적히고, 옆 칸에 작성자·시간·작성 중이 적힌다.
 *
 * 붙이는 법: 시트 → 확장 프로그램 → Apps Script → 이 파일 내용을 통째로 붙여 넣기
 *   → 배포 → 새 배포 → 유형 '웹 앱', 실행 사용자 '나', 액세스 '모든 사용자' → 배포
 *   → 나온 웹 앱 주소(…/exec)를 개발자에게 전달.
 */

// 입장 코드: 페이지에서 처음 한 번 넣는 코드. 바꾸면 '배포 관리 → 새 버전'으로 다시 배포.
var ENTRY_CODE = 'teamjun2026';

// 질문이 든 시트 (주소의 /d/ 뒤 값). 다른 시트를 쓰면 여기만 바꾼다.
var SHEET_ID = '15DHsI5LlvuYaLpn2Y3KY3B7z2ILGB0n7JP6lYtetmyw';

// '작성 중' 표시가 저절로 풀리는 시간(분)
var EDITING_MINUTES = 5;

var COL = { cat: 1, q: 2, answer: 3, by: 4, at: 5, editing: 6 };

function doGet(e) {
  return handle_(e && e.parameter ? e.parameter : {});
}

function doPost(e) {
  var body = {};
  try { body = JSON.parse(e.postData.contents); } catch (err) {}
  return handle_(body);
}

function handle_(p) {
  try {
    if (String(p.code || '') !== ENTRY_CODE) return out_({ ok: false, error: 'code' });
    var action = p.action || 'list';
    if (action === 'list') return out_(list_());

    var lock = LockService.getScriptLock();
    lock.waitLock(15000);
    try {
      if (action === 'save') return out_(save_(p));
      if (action === 'claim') return out_(claim_(p, true));
      if (action === 'release') return out_(claim_(p, false));
    } finally {
      lock.releaseLock();
    }
    return out_({ ok: false, error: 'action' });
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  }
}

function sheet_() {
  return SpreadsheetApp.openById(SHEET_ID).getSheets()[0];
}

// '분류' 머리줄이 몇 번째 줄인지 찾고, 옆 머리 칸(작성자·작성 시간·작성 중)을 채운다.
function headerRow_(sh, data) {
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][0]).trim() === '분류') {
      var r = i + 1;
      var names = ['작성자', '작성 시간', '작성 중'];
      for (var k = 0; k < names.length; k++) {
        var cell = sh.getRange(r, COL.by + k);
        if (!cell.getDisplayValue()) cell.setValue(names[k]);
      }
      return r;
    }
  }
  throw new Error('시트에서 "분류" 머리줄을 찾지 못했습니다');
}

function list_() {
  var sh = sheet_();
  var last = Math.max(sh.getLastRow(), 1);
  var rng = sh.getRange(1, 1, last, 6);
  var text = rng.getDisplayValues();
  var raw = rng.getValues();
  var head = headerRow_(sh, text);

  // 머리줄 위 안내(사이트 주소·관리자 들어가는 법 등)
  var notice = [];
  for (var i = 0; i < head - 1; i++) {
    for (var c = 0; c < 3; c++) if (String(text[i][c]).trim()) notice.push(String(text[i][c]).trim());
  }

  var now = Date.now();
  var items = [];
  var cur = null;
  for (var r = head; r < text.length; r++) {
    var a = String(text[r][0]).trim();
    var b = String(text[r][1]).trim();
    if (a && b) {
      var at = raw[r][COL.at - 1];
      var ed = parseEditing_(String(text[r][COL.editing - 1]), now);
      cur = {
        row: r + 1,
        cat: a,
        q: b,
        notes: [],
        answer: String(text[r][COL.answer - 1]),
        by: String(text[r][COL.by - 1]),
        at: at instanceof Date ? at.getTime() : null,
        editing: ed
      };
      items.push(cur);
    } else if (!a && b && cur) {
      cur.notes.push(b.replace(/^·\s*/, ''));
    } else if (!a && !b) {
      cur = null;
    }
  }
  return { ok: true, notice: notice, items: items, now: now };
}

function parseEditing_(s, now) {
  if (!s) return null;
  var parts = s.split('|');
  var t = Number(parts[1]);
  if (!parts[0] || !t || now - t > EDITING_MINUTES * 60000) return null;
  return { name: parts[0], at: t };
}

function checkRow_(sh, row) {
  row = Number(row);
  var data = sh.getRange(1, 1, Math.max(sh.getLastRow(), 1), 2).getDisplayValues();
  var head = headerRow_(sh, data);
  if (!row || row <= head || row > data.length) throw new Error('없는 질문입니다');
  if (!String(data[row - 1][0]).trim() || !String(data[row - 1][1]).trim()) throw new Error('질문 줄이 아닙니다');
  return row;
}

// base: 이 사람이 쓰기 시작할 때 본 답. 그사이 다른 사람이 바꿨으면 force 없이는 저장하지 않는다.
function save_(p) {
  var sh = sheet_();
  var row = checkRow_(sh, p.row);
  var name = String(p.name || '').trim().slice(0, 30);
  if (!name) return { ok: false, error: 'name' };
  var answer = String(p.answer == null ? '' : p.answer).slice(0, 5000);

  var current = sh.getRange(row, COL.answer).getDisplayValue();
  var currentBy = sh.getRange(row, COL.by).getDisplayValue();
  if (!p.force && current !== String(p.base == null ? '' : p.base)) {
    return { ok: false, error: 'conflict', answer: current, by: currentBy };
  }

  var ans = sh.getRange(row, COL.answer);
  ans.setNumberFormat('@');
  ans.setValue(answer);
  sh.getRange(row, COL.by).setValue(answer ? name : '');
  sh.getRange(row, COL.at).setValue(answer ? new Date() : '');
  sh.getRange(row, COL.editing).setValue('');
  return { ok: true };
}

function claim_(p, on) {
  var sh = sheet_();
  var row = checkRow_(sh, p.row);
  var name = String(p.name || '').trim().slice(0, 30);
  var cell = sh.getRange(row, COL.editing);
  if (on) {
    cell.setNumberFormat('@');
    cell.setValue(name + '|' + Date.now());
  } else if (cell.getDisplayValue().split('|')[0] === name) {
    cell.setValue('');
  }
  return { ok: true };
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
