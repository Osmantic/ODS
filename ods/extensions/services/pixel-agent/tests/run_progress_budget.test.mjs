import test from 'node:test';
import assert from 'node:assert/strict';
import {createRunProgressBudget, failedToolOutcome, isLiteralEcho, lookupFoundNothing, nodeReportedFailure, RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

test('a single lookup found nothing only when it completed with exit 1 and printed no error', () => {
  const receipt = (exitCode, text = '', details = {}) => ({content:[{type:'text', text:`${text}\n\n(Command exited with code ${exitCode})`}],
    details:{status:'completed', exitCode, durationMs:3, ...details}});
  const empty = receipt(1);
  for (const command of [
    'grep -n "function drawEnemyEnt" -A 12 chars.js',
    'cd Playground/elf-of-doom && grep -n "function drawEnemyEnt" -A 12 chars.js',
    "cd 'Playground/elf of doom' && grep -rn 'hitSet|fromEnemy' *.js",
    'grep -rn "a\\|b" .', 'egrep -n "x" f', 'fgrep -n x f', 'rg -n "x" src', 'git grep -n "x"', 'which node nodejs deno bun',
    '  grep -n x f  ',
  ]) assert.equal(lookupFoundNothing(command, empty), true, command);
  for (const command of [
    'grep -n x f | head', 'cat f | grep x', 'grep x f; echo $?', 'grep x f || true', 'grep x f && echo y',
    'grep x f > out', 'grep x f 2>/dev/null', 'grep x $(ls)', 'grep x `ls`', 'grep x f\nrm f', 'grep x f &',
    'cd a && cd b && grep x f', 'echo grep x', 'grepx -n f', 'sh -c "grep x f"', 'find . -name x', 'test -f x',
    'diff a b', 'command -v node', 'python3 -c "print(1)"', 'grep "unclosed', 'grep x\\ y f', '', 42, undefined,
  ]) assert.equal(lookupFoundNothing(command, empty), false, String(command));
  const search = 'grep -n x f';
  for (const result of [
    receipt(0), receipt(2), receipt(127), receipt(128), receipt(1, 'grep: f: No such file or directory'),
    receipt(1, '', {status:'running'}), receipt(1, '', {status:'failed'}), receipt(1, '', {sessionId:'s-1'}),
    {content:[{type:'text', text:''}], details:{status:'completed', exitCode:1.5}},
    {content:[{type:'text', text:''}], details:{status:'completed'}}, {details:{status:'completed', exitCode:1, aggregated:'grep: f: Permission denied'}},
    undefined, null, {},
  ]) assert.equal(lookupFoundNothing(search, result), false, JSON.stringify(result));
  assert.equal(lookupFoundNothing(search, {details:{status:'completed', exitCode:1, aggregated:''}}), true);
  // A count of zero and `which` resolving only some names are empty answers too.
  assert.equal(lookupFoundNothing('grep -c foo ui.js', receipt(1, '0')), true);
  assert.equal(lookupFoundNothing('which node deno', receipt(1, '/usr/bin/node')), true);
  assert.equal(lookupFoundNothing('grep '.padEnd(4100, 'x'), empty), false);
});

test('a chain of read-only searches whose last search came up empty found nothing', () => {
  const receipt = (text, exitCode = 1) => ({content:[{type:'text', text:`${text}\n\n(Command exited with code ${exitCode})`}],
    details:{status:'completed', exitCode, durationMs:5}});
  // The chains Portal's agent ran on the RTX Spark, with the output it got.
  const chains = [
    ['cd Playground/elf-of-doom && grep -n "shadowBlob\\|vShade\\|shadowBlur" chars.js legacy.js | head -40; echo "=== counts"; grep -c "shadowBlob" chars.js legacy.js',
      'chars.js:312:  g.shadowColor = "#7fd0ff"; g.shadowBlur = 10;\n=== counts\nchars.js:0\nlegacy.js:0'],
    ['cd Playground/elf-of-doom && grep -rn "hitSet\\|new Set(" *.js | cat; echo ---; grep -n "function startShot" -A 30 ui.js',
      'engine.js:244:  G.projectiles.push({ x: P.x, fromEnemy: false });\n---'],
    ['cd Playground/elf-of-doom && grep -n "function burst" -A 12 ui.js; grep -n "function popText" -A 6 ui.js; grep -n "function supplyTick" -A 2 ui.js', ''],
    ["cd Playground/elf-of-doom && sed -n '860,900p' ui.js; echo ===; grep -n \"function showScreen\" -A 15 ui.js",
      '  updateHud();\n  requestAnimationFrame(frame);\n==='],
    ['grep -c x a.js | sort | uniq -c && git grep -n "y" | tail -n 5 && which deno', '      1 a.js:2\nsrc/main.js:4:y'],
    ['grep -n "showScreen" ui.js; rg -n "function showScreen" src', 'ui.js:12:  showScreen("title");'],
    ['grep -rn foo . | tail -n +5 | sort -u | uniq -c; grep -n zzz ui.js', '      1 ./a.js:9:foo'],
    // grep's own notices on a search that succeeded are not errors, nor is quoting an error message.
    ['grep -rn "ui.js" .; grep -n zzz ui.js', './index.html:9:<script src="ui.js"></script>\ngrep: ./.git/index: binary file matches'],
    ['grep -n "\\d" ui.js; grep -n zzz ui.js', 'grep: warning: stray \\ before d'],
    ['grep -rn "Permission denied" src; grep -n zzz ui.js', "src/a.js:3:  log('Permission denied');"],
  ];
  for (const [command, output] of chains) assert.equal(lookupFoundNothing(command, receipt(output)), true, command);
  // An earlier step that failed prints an error, even where rg 13 leaves out its name.
  for (const [chain, output] of [
    ['rg -n "showScreen(" ui.js; grep -n "function showScreen" ui.js', 'regex parse error:\n    showScreen(\n              ^\nerror: unclosed group'],
    ['rg --bogus x ui.js; grep -n zzz ui.js', "error: Found argument '--bogus' which wasn't expected, or isn't valid in this context"],
    ['git grep --bogus x; grep -n zzz ui.js', "error: unknown option `bogus'\nusage: git grep [<options>] [-e] <pattern> [<rev>...] [[--] <path>...]"],
    ['which -z node; grep -n zzz ui.js', 'Illegal option -z\nUsage: /usr/bin/which [-as] args'],
    ['rg -n x missing.js; grep -n zzz ui.js', 'missing.js: No such file or directory (os error 2)'],
    ['grep -n x ui.js; grep -n zzz src', 'grep: src: Is a directory'],
  ]) assert.equal(lookupFoundNothing(chain, receipt(output)), false, chain);
  const [command, output] = chains[0];
  for (const result of [receipt(output, 0), receipt(output, 2), receipt(`${output}\ngrep: legacy.js: No such file or directory`),
    receipt(`sh: 1: cd: can't cd to Playground/elf-of-doom\n${output}`),
    {...receipt(output), details:{status:'completed', exitCode:1, sessionId:'s-1'}}]) {
    assert.equal(lookupFoundNothing(command, result), false, JSON.stringify(result));
  }
  for (const chain of [
    'grep a f; echo ===', 'grep a f; grep b f | head', 'grep a f; rm f; grep b f', 'grep a f | sh; grep b f',
    'grep a f | tee out; grep b f', "sed -i '1d' f; grep b f", "sed -n '1p' -i f; grep b f", "sed -n '1w out' f; grep b f",
    'grep a f;; grep b f', 'grep a f; grep b f;', 'grep a f |& cat; grep b f', 'grep a f & grep b f', 'grep a f || grep b f',
    'echo "$(rm x)"; grep b f', 'grep a f; cd x && grep b f', 'cat f; grep b f', 'grep a f | xargs rm; grep b f',
    'grep a f # ; grep b f', 'grep "a\\\nb" f; grep b f', "grep 'a\nb' f; grep b f", 'grep "a f; grep b f',
    // Options that write files or run programs, however they are quoted.
    "sed -n '1p' ui.js '-i'; grep -n zzz ui.js", 'sed -n 1p ui.js "--in-place"; grep -n zzz ui.js', "sed -n '0p' f; grep b f",
    'grep -rn foo . | sort -omakefile; grep -n zzz ui.js', 'grep -rn foo . | sort -c; grep -n zzz ui.js', 'grep a f | head 5; grep b f',
    "git grep -O'rm -f' foo; grep -n zzz ui.js", 'git grep -nOrm foo; grep -n zzz ui.js', 'git grep --open-files-in-pager=rm foo; grep -n zzz ui.js',
    'rg --pre rm -n x victim.txt', 'rg --pre=rm x victim.txt', "rg --p're' rm x f", 'git -C src grep x',
    // rg 13 prints some errors without a prefix, so rg may only be the last step.
    'rg -n -t jsx showScreen src; grep -n zzz ui.js', 'rg -t jsx x src | head -5 && grep zzz f', 'rg -n "a" f && grep b f',
  ]) assert.equal(lookupFoundNothing(chain, receipt('')), false, chain);
});

test('a node check, test or harness reported a failure only when it completed with exit 1 and printed it', () => {
  const receipt = (exitCode, text, details = {}) => ({content:[{type:'text', text:`${text}\n\n(Command exited with code ${exitCode})`}],
    details:{status:'completed', exitCode, durationMs:3, ...details}});
  const red = receipt(1, 'ui.js:3\n  ctx.save(\n          ^\n\nSyntaxError: missing ) after argument list');
  for (const command of [
    'node --check ui.js', 'node -c ui.js', "node --check 'elf of doom/ui.js'", 'node --check src/a.mjs', 'node --check types.ts',
    'node --test', 'node --test logic.test.mjs', 'node --test --test-reporter=spec tests/a.test.mjs tests/b.test.cjs',
    'node harness.cjs ui.js', 'node --experimental-vm-modules harness.mjs', 'node -- harness.js',
    'timeout 60 node harness.cjs ui.js', 'timeout -k 5 60s node bench.mjs', 'timeout --signal=KILL 1m node --test t.mjs',
    'node --test t.mjs 2>&1', 'cd Playground/elf-of-doom && node --check ui.js && node --check chars.js 2>&1',
    'node --check ui.js; timeout 60 node harness.cjs ui.js', '  node --check ui.js  ',
  ]) assert.equal(nodeReportedFailure(command, red), true, command);
  for (const command of [
    'node', 'node -', 'node -e "x()"', 'node -p 1', 'node -pe 1', 'node --eval=x', 'node --print x', 'node -i', 'node --interactive',
    'node --run test', 'node --eval ./run.js', 'node --print=1 x.js', 'node --run t.js', 'node --interactive x.js', 'node -r ./setup.js t.js', 'node --check', 'node --check a.js b.js', 'node --check --test a.js', 'node --check -',
    'node --test -', 'node notes.txt', 'node t.js > out.txt', 'node t.js 2>/dev/null', 'node t.js 2>&1x', 'node t.js &',
    'node --test t.mjs | tail -20', 'node --test t.mjs || true', 'node "$f"', 'node `ls`', 'node $(ls)', 'NODE_OPTIONS=x node t.js',
    'for f in *.js; do node --check "$f" || exit 1; done', 'echo ok; node --test t.mjs', 'node --test t.mjs; echo done',
    'nodejs t.js', 'timeout node t.js', 'timeout --bogus 60 node t.js', 'env node t.js', 'npm test', 'npx node t.js',
    'cd a && cd b && node t.js', 'node t.js\nrm -rf x', '', 42, undefined, 'node '.padEnd(4100, 'x') + '.js',
  ]) assert.equal(nodeReportedFailure(command, red), false, String(command));
  for (const result of [
    receipt(0, 'ok'), receipt(2, 'boom'), receipt(9, 'node: bad option: --bogus'), receipt(124, ''), receipt(127, 'sh: 1: node: not found'),
    receipt(130, 'interrupted'), receipt(134, 'FATAL ERROR: Reached heap limit'), receipt(137, ''), receipt(1, ''), receipt(1, ' \n '),
    receipt(1, "Error: Cannot find module '/workspace/x/ui.js'\n  code: 'MODULE_NOT_FOUND',"),
    receipt(1, "Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/workspace/x/nope.mjs'"),
    receipt(1, "Could not find '/workspace/x/missing.test.mjs'"),
    receipt(1, "Error: ENOENT: no such file or directory, open 'ui.js'"),
    receipt(1, "timeout: failed to run command 'node': No such file or directory"),
    receipt(1, 'sh: 1: cd: can\'t cd to Playground'), receipt(1, 'Segmentation fault'),
    receipt(1, 'x', {status:'running'}), receipt(1, 'x', {status:'failed'}), receipt(1, 'x', {sessionId:'s-1'}),
    {content:[{type:'text', text:'x'}], details:{status:'completed', exitCode:1.5}}, {content:[{type:'text', text:'x'}], details:{status:'completed'}},
    {details:{status:'completed', exitCode:1, aggregated:''}}, undefined, null, {},
  ]) assert.equal(nodeReportedFailure('node --check ui.js', result), false, JSON.stringify(result));
  assert.equal(nodeReportedFailure('node --test t.mjs', {details:{status:'completed', exitCode:1, aggregated:'not ok 1 - steps\n# fail 1'}}), true);
  assert.equal(nodeReportedFailure('node harness.cjs ui.js', receipt(1, '{"frames":60,"saveDepth":60}')), true);
  assert.equal(nodeReportedFailure('node throw.cjs', receipt(1, "TypeError: Cannot read properties of undefined (reading 'hp')")), true);
  // The merged-stderr spelling is accepted for node only; lookups keep their rules.
  assert.equal(lookupFoundNothing('grep -n x ui.js 2>&1', receipt(1, '')), false);
});

test('outer and nested failure receipts do not masquerade as successful progress', () => {
  for (const event of [{error:'unavailable'}, {result:{isError:true}},
    {result:{details:{exitCode:1}}}, {result:{details:{status:'blocked'}}},
    {result:{details:{result:{isError:true}}}}]) assert.equal(failedToolOutcome(event), true);
  assert.equal(failedToolOutcome({result:{content:[{type:'text',text:'Example error: failed'}],details:{exitCode:0}}}), false);
});

test('consecutive malformed calls trip a sticky run-wide fuse', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 4; i++) {
    budget.beginModelRound();
    budget.observeResult({callId: `call-${i}`, tool:'tool_call', failed:true});
    assert.equal(budget.exhausted, i === 3);
  }
  budget.observeResult({callId:'recovery', tool:'read', params:{path:'file'}, failed:false});
  assert.equal(budget.exhausted, true);
});

test('alternating discovery and failures cannot reset consecutive failure allowance', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 4; i++) {
    budget.observeResult({callId:`search-${i}`, tool:'tool_search', params:{query:String(i)}, failed:false});
    budget.observeResult({callId:`bad-${i}`, tool:'tool_call', failed:true});
    assert.equal(budget.exhausted, i === 3);
  }
  assert.equal(budget.exhausted, true);
});

test('discovery-only rounds are bounded even with distinct successful searches', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 8; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`discover-${i}`, tool:i % 2 ? 'tool_describe' : 'tool_search',
      params:{query:String(i)}, failed:false});
  }
  assert.equal(budget.beginModelRound(), true);
});

test('missing tool hooks are bounded at model continuation level', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 8; i++) assert.equal(budget.beginModelRound(), false);
  assert.equal(budget.beginModelRound(), true);
});

test('genuine progress permits long tasks; repeated success is not endless progress', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 100; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`read-${i}`, tool:'read', params:{path:`file-${i}`}, failed:false});
  }
  for (let i = 0; i < 12; i++) {
    budget.beginModelRound();
    budget.observeResult({callId:`echo-${i}`, tool:'exec', params:{command:'echo done'}, failed:false});
  }
  assert.equal(budget.exhausted, true);
});

test('after-tool and persist delivery of one failure count only once', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 30; i++) budget.observeResult({callId:'same', failed:true});
  assert.equal(budget.exhausted, false);
  for (let i = 0; i < 3; i++) budget.observeResult({callId:`next-${i}`, failed:true});
  assert.equal(budget.exhausted, true);
});

test('caller-classified metadata cannot reset failures or count as task progress', () => {
  const budget=createRunProgressBudget();
  for(let i=0;i<4;i++) {
    budget.observeResult({callId:`failure-${i}`,tool:'exec',failed:true});
    budget.observeResult({callId:`metadata-${i}`,tool:'tool_call',failed:false,discovery:true});
    assert.equal(budget.exhausted,i===3);
  }
  const failedMetadata=createRunProgressBudget();
  for(let i=0;i<4;i++) failedMetadata.observeResult({callId:`metadata-${i}`,tool:'tool_call',failed:true,discovery:true});
  assert.equal(failedMetadata.exhausted,true,'metadata errors still count');
});

test('mixed failure streaks suspend only their lane and remain sticky after sibling progress', () => {
  const budget=createRunProgressBudget();
  for(let i=0;i<4;i++) {
    budget.observeResult({callId:`extension-${i}`,tool:'prepare',lane:'extension',failed:true});
    budget.observeResult({callId:`metadata-${i}`,tool:'status',lane:'extension',discovery:true,failed:false});
    budget.observeResult({callId:`workspace-${i}`,tool:'write',params:{path:`file-${i}`},lane:'workspace',failed:false});
  }
  assert.equal(budget.exhausted,false);
  assert.deepEqual(budget.exhaustedLanes,['extension']);
  budget.observeResult({callId:'late-success',tool:'prepare',lane:'extension',failed:false});
  assert.equal(budget.laneExhausted('extension'),true);
  assert.equal(budget.laneExhausted('workspace'),false);
});

test('mixed lanes retain global total and no-progress ceilings', () => {
  const total=createRunProgressBudget();
  for(let i=0;i<12;i++) {
    total.observeResult({callId:`failure-${i}`,lane:'extension',failed:true});
    assert.equal(total.exhausted,i===11);
  }
  const rounds=createRunProgressBudget();
  for(let i=0;i<8;i++) {
    assert.equal(rounds.beginModelRound(),false);
    rounds.observeResult({callId:`metadata-${i}`,lane:'extension',tool:'status',discovery:true,failed:false});
  }
  assert.equal(rounds.beginModelRound(),true);
});

test('unclassified failures and two exhausted lanes still stop the full run', () => {
  for(const lane of [undefined,'model-supplied-fake-lane']) {
    const budget=createRunProgressBudget();
    for(let i=0;i<4;i++) budget.observeResult({callId:`unknown-${i}`,lane,failed:true});
    assert.equal(budget.exhausted,true);
  }
  const both=createRunProgressBudget();
  for(const lane of ['extension','workspace']) for(let i=0;i<4;i++) {
    both.observeResult({callId:`${lane}-${i}`,lane,failed:true});
    both.observeResult({callId:`${lane}-${i}`,failed:true});
  }
  assert.deepEqual(both.exhaustedLanes,['extension','workspace']);
  assert.equal(both.exhausted,true);
});

test('an external stop is sticky and changes no limit', () => {
  const budget = createRunProgressBudget();
  budget.stop();
  assert.equal(budget.exhausted, true);
  budget.observeResult({callId: 'later', tool: 'read', params: {path: 'x'}, failed: false});
  assert.equal(budget.exhausted, true);
  assert.deepEqual(RUN_PROGRESS_LIMITS, {consecutiveFailures: 4, totalFailures: 12, roundsWithoutProgress: 8, identicalSuccesses: 2});
});

test('verified pending process receipts do not exhaust progress rounds', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 100; i++) {
    assert.equal(budget.beginModelRound(), false);
    budget.observeResult({callId:`poll-${i}`, tool:'process', pending:true, failed:false});
  }
});

test('read-only echo classification excludes substitutions, redirections and compound commands', () => {
  for (const command of ['echo "Done!"', "echo 'Site ready'", "echo 'literal $HOME' "]) assert.equal(isLiteralEcho(command), true, command);
  for (const command of ['echo "$HOME"', 'echo "$(touch bad)"', 'echo "`touch bad`"', 'echo "ok" > index.html', 'echo "ok"; rm file', 'echo "ok" && run', 'echo "a\\"', undefined]) assert.equal(isLiteralEcho(command), false, command);
});
