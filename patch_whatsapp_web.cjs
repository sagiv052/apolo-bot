'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageMain = require.resolve('whatsapp-web.js');
const utilsPath = path.join(
  path.dirname(packageMain),
  'src',
  'util',
  'Injected',
  'Utils.js',
);

if (!fs.existsSync(utilsPath)) {
  throw new Error(`Cannot find whatsapp-web.js injected utilities at ${utilsPath}`);
}

const source = fs.readFileSync(utilsPath, 'utf8');
if (source.includes('delete message.__x_id')) {
  console.log('whatsapp-web.js media-send fix is already present.');
  process.exit(0);
}

const anchor = [
  '        if (botOptions) {',
  '            delete message.canonicalUrl;',
  '        }',
].join('\n');
if (!source.includes(anchor)) {
  throw new Error('Unrecognized whatsapp-web.js Utils.js; media-send fix was not applied.');
}

const fix = [
  '        if (message.__x_id) {',
  '            delete message.__x_id;',
  '        }',
  '',
  anchor,
].join('\n');
fs.writeFileSync(utilsPath, source.replace(anchor, fix), 'utf8');
console.log('Applied whatsapp-web.js media-send fix.');
