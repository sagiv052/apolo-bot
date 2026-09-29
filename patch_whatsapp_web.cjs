'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageMain = require.resolve('whatsapp-web.js');
const packageRoot = path.dirname(packageMain);

function patchFile(filePath, marker, anchor, replacement, description) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Cannot find whatsapp-web.js file at ${filePath}`);
  }

  const source = fs.readFileSync(filePath, 'utf8');
  if (source.includes(marker)) {
    console.log(`${description} is already present.`);
    return;
  }
  if (!source.includes(anchor)) {
    throw new Error(`Unrecognized whatsapp-web.js source at ${filePath}; ${description} was not applied.`);
  }

  fs.writeFileSync(filePath, source.replace(anchor, replacement), 'utf8');
  console.log(`Applied ${description}.`);
}

const injectedUtilsPath = path.join(packageRoot, 'src', 'util', 'Injected', 'Utils.js');
const mediaAnchor = [
  '        if (botOptions) {',
  '            delete message.canonicalUrl;',
  '        }',
].join('\n');
const mediaFix = [
  '        if (message.__x_id) {',
  '            delete message.__x_id;',
  '        }',
  '',
  mediaAnchor,
].join('\n');
patchFile(
  injectedUtilsPath,
  'delete message.__x_id;',
  mediaAnchor,
  mediaFix,
  'the WhatsApp media-send compatibility fix',
);

const puppeteerUtilsPath = path.join(packageRoot, 'src', 'util', 'Puppeteer.js');
const bindingAnchor = [
  '    if (exist) {',
  '        return;',
  '    }',
  '    await page.exposeFunction(name, fn);',
].join('\n');
const bindingFix = [
  '    if (exist) {',
  '        return;',
  '    }',
  '    try {',
  '        await page.exposeFunction(name, fn);',
  '    } catch (error) {',
  '        // Another injection can create the same binding after the check above.',
  '        // In that race, the binding is already available, so continue safely.',
  '        if (!/already exists/i.test(String(error && error.message ? error.message : error))) {',
  '            throw error;',
  '        }',
  '    }',
].join('\n');
patchFile(
  puppeteerUtilsPath,
  'Another injection can create the same binding after the check above.',
  bindingAnchor,
  bindingFix,
  'the duplicate Puppeteer page-binding race fix',
);
