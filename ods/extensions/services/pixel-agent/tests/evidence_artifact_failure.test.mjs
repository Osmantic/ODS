import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createEvidenceArtifactWriter } from '../plugin/evidence-artifact.mjs';

test('a partial failed write preserves the previous verified report and cleans staging', () => {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'pixel-evidence-failure-'));
  const destination = path.join(root, 'report.txt');
  const original = 'Previous verified report\n';
  const replacement = 'Replacement report that cannot finish\n';
  fs.writeFileSync(destination, original, {mode:0o600});
  const write = fs.writeSync;
  try {
    fs.writeSync = (fd, buffer, offset, length, position) => {
      if (Buffer.isBuffer(buffer) && buffer.toString('utf8') === replacement) {
        write(fd, buffer, offset, 3, position);
        throw Object.assign(new Error('Simulated full filesystem'), {code:'ENOSPC'});
      }
      return write(fd, buffer, offset, length, position);
    };
    syncBuiltinESMExports();
    assert.throws(() => createEvidenceArtifactWriter({workspaceRoot:root})({
      relativePath:'report.txt', content:replacement,
    }), {code:'ENOSPC'});
  } finally {
    fs.writeSync = write;
    syncBuiltinESMExports();
  }
  try {
    assert.equal(fs.readFileSync(destination, 'utf8'), original);
    assert.deepEqual(fs.readdirSync(root), ['report.txt']);
    const result = createEvidenceArtifactWriter({workspaceRoot:root})({
      relativePath:'report.txt', content:replacement,
    });
    assert.equal(result.readbackVerified, true);
    assert.equal(fs.readFileSync(destination, 'utf8'), replacement);
    assert.deepEqual(fs.readdirSync(root), ['report.txt']);
  } finally {
    fs.rmSync(root, {recursive:true, force:true});
  }
});

test('a readback mismatch never replaces an existing report', () => {
  const root = fs.mkdtempSync(path.join(tmpdir(), 'pixel-evidence-readback-'));
  const destination = path.join(root, 'report.txt');
  fs.writeFileSync(destination, 'Keep this report', {mode:0o600});
  const read = fs.readSync;
  try {
    fs.readSync = (fd, buffer, offset, length, position) => {
      const count = read(fd, buffer, offset, length, position);
      if (count && buffer.toString('utf8') === 'New verified report') buffer[0] ^= 1;
      return count;
    };
    syncBuiltinESMExports();
    assert.throws(() => createEvidenceArtifactWriter({workspaceRoot:root})({
      relativePath:'report.txt', content:'New verified report',
    }), /readback mismatch/);
  } finally {
    fs.readSync = read;
    syncBuiltinESMExports();
  }
  try {
    assert.equal(fs.readFileSync(destination, 'utf8'), 'Keep this report');
    assert.deepEqual(fs.readdirSync(root), ['report.txt']);
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});

test('destination symlinks cannot replace the linked report', t => {
  if (process.platform === 'win32') return t.skip('Windows symlink creation needs additional privileges');
  const root = fs.mkdtempSync(path.join(tmpdir(), 'pixel-evidence-symlink-'));
  try {
    fs.writeFileSync(path.join(root, 'original.txt'), 'Keep', {mode:0o600});
    fs.symlinkSync('original.txt', path.join(root, 'report.txt'));
    assert.throws(() => createEvidenceArtifactWriter({workspaceRoot:root})({
      relativePath:'report.txt', content:'Replace',
    }), /unsafe Pixel evidence destination/);
    assert.equal(fs.readFileSync(path.join(root, 'original.txt'), 'utf8'), 'Keep');
    assert.equal(fs.lstatSync(path.join(root, 'report.txt')).isSymbolicLink(), true);
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});
