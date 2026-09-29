const COMMANDS = new Map([
  ['חיפוש משמרת חדשה', 'scan'],
  ['סריקת משמרת חדשה', 'scan'],
  ['סרוק משמרת חדשה', 'scan'],
  ['סריקה מיידית', 'scan'],
  ['רשימת אירועים', 'list_events'],
  ['רשימת פקודות', 'help'],
  ['רשימת עדכונים', 'help'],
  ['צילום מסך', 'screenshot'],
  ['צילום מסך אחרון', 'screenshot'],
]);

function actionFromBody(body) {
  const text = String(body || '').trim().replace(/\s+/g, ' ').replace(/[.!?؟,،]+$/u, '');
  if (/^שלח לי עדכונים(?:\s|$)/u.test(text)) return 'subscribe';
  if (/^הפסק עדכונים(?:\s|$)/u.test(text)) return 'unsubscribe';
  return COMMANDS.get(text) || null;
}

module.exports = actionFromBody;
