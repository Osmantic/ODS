const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/settings.js`, 'utf8');
function load(password, secret = 'a'.repeat(64)) {
    const env = {NODE_RED_ADMIN_PASSWORD: password, NODE_RED_CREDENTIAL_SECRET: secret};
    const ctx = {Buffer, process: {env}, module: {exports: {}}, require: name => {
        assert.equal(name, 'bcryptjs');
        return {hashSync: (value, rounds) => {
            assert.equal(value, password); assert.equal(rounds, 12); return 'test-hash';
        }};
    }};
    vm.runInNewContext(source, ctx);
    return {settings: ctx.module.exports, env};
}
test('password byte bounds reject bcrypt truncation without leaking values', () => {
    for (const password of ['', 'short', 'x'.repeat(73), 'é'.repeat(37)]) {
        assert.throws(() => load(password), error => {
            assert.equal(error.message, 'NODE_RED_ADMIN_PASSWORD must contain 16 to 72 UTF-8 bytes');
            return true;
        });
    }
    assert.equal(load('é'.repeat(36)).settings.adminAuth.users[0].password, 'test-hash');
});
test('stable encryption key is required and bootstrap values leave environment', () => {
    assert.throws(() => load('x'.repeat(16), 'bad-secret'), /64 hexadecimal/);
    const {settings, env} = load('x'.repeat(16));
    assert.equal(settings.credentialSecret, 'a'.repeat(64));
    assert.deepEqual(env, {});
    assert.equal(settings.httpNodeAuth.pass, settings.adminAuth.users[0].password);
    assert.equal(settings.adminAuth.users[0].permissions, '*');
});
