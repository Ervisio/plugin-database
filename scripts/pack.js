import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';

const distDir = path.resolve('dist/database');
const pluginDir = path.resolve('plugin');

if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

// Copy logo.svg
fs.copyFileSync(path.join(pluginDir, 'logo.svg'), path.join(distDir, 'logo.svg'));

// Copy db-bridge.py
fs.copyFileSync(path.resolve('db-bridge.py'), path.join(distDir, 'db-bridge.py'));
for (const file of ['db_runtime.py', 'db_runtime_windows.py', 'requirements.lock']) fs.copyFileSync(path.resolve(file), path.join(distDir, file));

// Read manifest
const manifest = JSON.parse(fs.readFileSync(path.join(pluginDir, 'manifest.json'), 'utf-8'));

function sha256(filePath) {
  const content = fs.readFileSync(filePath);
  return crypto.createHash('sha256').update(content).digest('hex');
}

manifest.files = {
  'index.js': sha256(path.join(distDir, 'index.js')),
  'logo.svg': sha256(path.join(distDir, 'logo.svg')),
  'db-bridge.py': sha256(path.join(distDir, 'db-bridge.py')),
  'db_runtime.py': sha256(path.join(distDir, 'db_runtime.py')),
  'db_runtime_windows.py': sha256(path.join(distDir, 'db_runtime_windows.py')),
  'requirements.lock': sha256(path.join(distDir, 'requirements.lock'))
};

fs.writeFileSync(path.join(distDir, 'manifest.json'), JSON.stringify(manifest, null, 2));

console.log('✅ Plugin staged successfully in dist/database/:');
console.log(JSON.stringify(manifest.files, null, 2));

// If running pack command
if (process.argv.includes('pack')) {
  const pkg = JSON.parse(fs.readFileSync(path.resolve('package.json'), 'utf-8'));
  const version = pkg.version;
  const id = manifest.id;
  if (manifest.version !== version || (process.env.TAG && process.env.TAG !== `v${version}`)) throw new Error('Tag, manifest and package versions must match.');
  const tarName = `${id}-${version}.tar.gz`;
  const tarPath = path.resolve('dist', tarName);
  const shaPath = path.resolve('dist', `${tarName}.sha256`);

  execFileSync('tar', ['-czf', tarPath, '-C', path.resolve('dist'), id]);
  const hash = sha256(tarPath);
  fs.writeFileSync(shaPath, `${hash}  ${tarName}\n`);
  console.log(`📦 Release package created: ${tarName} (sha256: ${hash})`);
}
