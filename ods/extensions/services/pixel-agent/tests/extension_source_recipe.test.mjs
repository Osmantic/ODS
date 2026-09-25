import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {compileSourceRecipe} from '../plugin/extension-source-recipe.mjs';

const source = {repository: 'https://github.com/example/project', commit: 'a'.repeat(40),
  serviceId: 'example-project', name: 'Example Project', dockerfile: 'deploy/Dockerfile',
  port: 8080, healthPath: '/health', healthcheck: ['CMD', 'curl', '-f', 'http://localhost:8080/health']};

test('compiles only packaging conventions and preserves the researched application', () => {
  const recipe = compileSourceRecipe(source);
  assert.equal(recipe.manifest.service.port, 8080);
  assert.equal(recipe.manifest.service.external_port_env, 'EXAMPLE_PROJECT_PORT');
  const service = recipe.compose.services['example-project'];
  assert.deepEqual(service.build, {context: source.repository + '.git#' + source.commit, dockerfile: 'deploy/Dockerfile'});
  assert.equal(service.image, 'ods-source-example-project:' + source.commit);
  assert.equal(service.container_name, 'ods-example-project');
  assert.deepEqual(service.ports, ['127.0.0.1:${EXAMPLE_PROJECT_PORT:-8080}:8080']);
  assert.deepEqual(service.healthcheck.test, source.healthcheck);
  assert.equal(service.command, undefined);
});

test('inline shell variables remain in the container, not host interpolation', () => {
  const {dockerfile, ...inline} = source;
  const recipe = compileSourceRecipe({...inline, dockerfileInline: 'FROM python:3.12-slim\nCOPY . /app\nRUN echo "$HOME"\n',
    command: ['sh', '-c', 'exec app "$PORT"']});
  const service = recipe.compose.services['example-project'];
  assert.match(service.build.dockerfile_inline, /"\$\$HOME"/);
  assert.equal(service.command[2], 'exec app "$$PORT"');
});

test('a registry package alone is not the requested immutable source', () => {
  const {dockerfile, ...inline} = source;
  assert.throws(() => compileSourceRecipe({...inline,
    dockerfileInline: 'FROM python:3.12-slim\nRUN pip install unrelated\n'}), /checked-out repository source/);
});

test('Python packaging installs the pinned checkout and retains the explicit application command', () => {
  const {dockerfile, healthPath, healthcheck, ...python} = source;
  const command = ['python', '-c', 'from actual_package import PublicAPI'];
  const recipe = compileSourceRecipe({...python, port:0, cliOnly:true, pythonVersion:'3.12', command});
  const service = recipe.compose.services['example-project'];
  assert.equal(service.build.context, source.repository + '.git#' + source.commit);
  assert.match(service.build.dockerfile_inline, /^FROM python:3\.12-slim\n/);
  assert.match(service.build.dockerfile_inline, /apt-get install -y --no-install-recommends git/);
  const inlineDockerfile = service.build.dockerfile_inline;
  assert.ok(inlineDockerfile.indexOf('apt-get install -y --no-install-recommends git')
    < inlineDockerfile.indexOf('git fetch --quiet --unshallow --tags origin')
    && inlineDockerfile.indexOf('git fetch --quiet --unshallow --tags origin')
      < inlineDockerfile.indexOf('RUN python -m pip install --no-cache-dir .'),
  'SCM tag history must be present before pip resolves a dynamic version');
  assert.match(inlineDockerfile, /git rev-parse HEAD \| grep -Fx 'a{40}'/);
  assert.match(inlineDockerfile, /COPY \. \.\nRUN if .*\nRUN python -m pip install --no-cache-dir \. && python -m pip check/);
  assert.deepEqual(service.command, command);
  assert.equal(service.healthcheck, undefined);
  for (const change of [{pythonVersion:'3.12\nRUN bad'}, {pythonVersion:'latest'},
    {dockerfile:'Dockerfile'}, {dockerfileInline:'FROM python:3.12\nCOPY . .'}, {command:undefined}]) {
    assert.throws(() => compileSourceRecipe({...python,port:0,cliOnly:true,pythonVersion:'3.12',command,...change}));
  }
  assert.throws(() => compileSourceRecipe({...source,dockerfile:undefined,pythonVersion:'3.12'}), /entrypoint/);
});

test('CLI verification does not invent a server, port or background process', () => {
  const {healthPath, healthcheck, ...cli} = source;
  const recipe = compileSourceRecipe({...cli, port: 0, cliOnly: true,
    command: ['python', '-m', 'upstream_cli', '--version']});
  assert.equal(recipe.manifest.service.startup_check, false);
  assert.equal(recipe.manifest.service.external_link, false);
  assert.equal(recipe.compose.services['example-project'].ports, undefined);
  assert.equal(recipe.compose.services['example-project'].restart, undefined);
  assert.equal(recipe.compose.services['example-project'].healthcheck, undefined);
  assert.equal(recipe.manifest.service.health, '');
  assert.throws(() => compileSourceRecipe({...source, port: 0, healthPath: '', cliOnly: true}));
});

test('Python library verification imports actual modules outside the source checkout', () => {
  const {dockerfile, healthPath, healthcheck, ...library} = source;
  const profile = {...library, port:0, cliOnly:true, pythonVersion:'3.12', pythonImports:['actual_package','actual_package.api']};
  const recipe = compileSourceRecipe(profile);
  const service = recipe.compose.services['example-project'];
  assert.deepEqual(service.command, ['python','-c','import importlib; [importlib.import_module(name) for name in ["actual_package","actual_package.api"]]']);
  assert.match(service.build.dockerfile_inline, /pip check\nWORKDIR \/opt\/ods\n$/);
  for (const change of [{pythonImports:[]}, {pythonImports:['x; print(1)']}, {pythonImports:['../secret']},
    {pythonImports:['x\"']}, {command:['invented-cli']}, {cliOnly:false}, {pythonVersion:undefined}]) {
    assert.throws(() => compileSourceRecipe({...profile,...change}));
  }
});

test('optional Python function check compares documented output in the managed verification command', t => {
  const {dockerfile, healthPath, healthcheck, ...library} = source;
  const profile = {...library, port:0, cliOnly:true, pythonVersion:'3.12', pythonImports:['json'],
    pythonVerification:{expression:'modules["json"].dumps({"a": 1}, sort_keys=True)', expected:'{"a": 1}'}};
  const recipe = compileSourceRecipe(profile);
  const command = recipe.compose.services['example-project'].command;
  assert.deepEqual(command.slice(0,2), ['python','-c']);
  assert.match(command[2], /importlib\.import_module/);
  assert.match(command[2], /assert str\(actual\) ==/);
  assert.doesNotMatch(command[2], /subprocess|os\.system/);
  const executable = process.platform === 'win32' ? 'python' : 'python3';
  const run = code => spawnSync(executable, ['-c', code], {encoding:'utf8',timeout:10000});
  const success = run(command[2]);
  if (success.error?.code === 'ENOENT') return t.skip('Python executable is unavailable');
  assert.equal(success.status,0,success.stderr);
  const mismatch = compileSourceRecipe({...profile,
    pythonVerification:{...profile.pythonVerification,expected:'wrong'}});
  const failure = run(mismatch.compose.services['example-project'].command[2]);
  assert.notEqual(failure.status,0);
  assert.match(failure.stderr,/Documented Python function returned an unexpected value/);
  const invalid = compileSourceRecipe({...profile,
    pythonVerification:{...profile.pythonVerification,expression:'1 +' }});
  assert.notEqual(run(invalid.compose.services['example-project'].command[2]).status,0);
  for (const verification of [null,{}, {expression:'',expected:'x'},
    {expression:'x'.repeat(2049),expected:'x'}, {expression:'1',expected:'x'.repeat(2049)},
    {expression:'1',expected:'x',command:'bad'}]) {
    assert.throws(() => compileSourceRecipe({...profile,pythonVerification:verification}));
  }
  assert.throws(() => compileSourceRecipe({...profile,pythonImports:undefined}));
});

test('invalid identities, ambiguous Dockerfiles and unsupported host fields are rejected', () => {
  for (const change of [{commit: 'main'}, {repository: 'http://localhost/repo'}, {serviceId: '../host'},
    {dockerfileInline: 'FROM alpine'}, {dockerfile: ''}, {port: '8080'}, {healthPath: ''},
    {cliOnly: true}, {privileged: true}, {constructor: 'bad'}, {healthcheck: ['NONE']}]) {
    assert.throws(() => compileSourceRecipe({...source, ...change}));
  }
});
