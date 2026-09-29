const COMMANDS = new Map([
  ['חיפוש משמרת חדשה', 'scan'],
  ['רשימת פקודות', 'help'],
  ['צילום מסך', 'screenshot'],
]);

function actionFromBody(body) {
  const text = String(body || '').trim().replace(/\s+/g, ' ').replace(/[.!?؟,،]+$/u, '');
  if (/^שלח לי עדכונים(?:\s|$)/u.test(text)) return 'subscribe';
  if (/^הפסק עדכונים(?:\s|$)/u.test(text)) return 'unsubscribe';
  return COMMANDS.get(text) || null;
}

module.exports = actionFromBody;
