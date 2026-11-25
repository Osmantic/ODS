import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');

// Observed on an RTX Spark: game feedback for a Playground canvas game named
// the laptop the owner viewed it on, and "start" in "start screen" read as a
// host action. Every tool, workspace read included, was then blocked until
// the run-progress guard ended the turn.
const SPARK_SENTENCE =
  "On my laptop the start screen gets cut off, the top icon is chopped and I can't see the Try ODS link at all.";
const SPARK_FEEDBACK =
  `Some feedback on the elf game. ${SPARK_SENTENCE} Only work in Playground/elf-of-doom.`;

function call(guard, toolName, params) {
  return guard.beforeToolCall(
    {toolName, runId: 'run-1', params},
    {agentId: 'pixel', toolName, runId: 'run-1', sessionId: 'session-1'},
    'pixel');
}

function observe(prompt) {
  const guard = api.createToolLoopGuard();
  guard.observeRun({agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'}, 'pixel', {prompt});
  return guard;
}

for (const text of [
  SPARK_SENTENCE,
  SPARK_FEEDBACK,
  // A UI noun names part of the owner's project.
  'The start menu on my machine is too big.',
  'The start button is hidden on my laptop.',
  'The start page on this computer loads slowly.',
  'On this machine the restart button does nothing.',
  'The update screen on my computer flickers.',
  'On my laptop the install page is blank.',
  'On my machine it looks fine at the start of the level.',
  'The change log on my laptop is empty.',
  'The changelog on my laptop is empty.',
  'The start and restart buttons overlap on my laptop.',
  'The title screen and start screens on my laptop get cut off.',
  'The start/restart buttons on my laptop overlap.',
  'The "Start" button is hidden on my laptop.',
  'The “Start” button is hidden on my laptop.',
  // A preposition, quantifier or button press names a moment in the game.
  'This computer shows a black screen at start.',
  'This computer shows a black screen on restart.',
  'This computer shows a black screen after update.',
  'Every restart resets the score on my laptop.',
  'Each restart resets the score on my laptop.',
  'After the latest update the game is slow on my laptop.',
  'The new update broke the game on my laptop.',
  'The last restart fixed it on my laptop.',
  'The restart button freezes my laptop when I press start.',
  // A negation describes what a thing did.
  "The game won't start on my laptop.",
  "On my laptop the game doesn't update the score.",
  "The game can't start on my laptop.",
  "My laptop can't run the game.",
  'The game does not start on my laptop.',
  'The game did not start on my machine.',
  'The game will not start on my laptop.',
  'Why does the game not start on my laptop?',
  'It fails to start on my laptop.',
  'It refuses to start on my laptop.',
  // The owner describes their own action.
  'When I restart the level on my machine it crashes.',
  'When I start the game on my laptop it freezes.',
  'It crashes on my laptop when I start the game.',
  'The game crashes when I run it on my laptop.',
  'My laptop gets hot when I run the game.',
  'I tried to start the game on my laptop and it froze.',
  'Also, on my laptop it crashes after I restart.',
  'One more thing: on my laptop the game crashes when I restart.',
  'Looks great on desktop but on my laptop it crashes when I restart.',
  'The start screen froze my laptop and I had to restart.',
  'The install page crashed my laptop when I tried to run it.',
  'The start menu hangs my laptop until I stop the game.',
  'The update broke my laptop when I start the game.',
  'Work in Playground/elf-of-doom only. When I restart the level on my laptop it crashes.',
  // Without an article, a UI noun or a finite verb after the word still makes
  // it a name or a subject.
  'Start screen is cut off on my laptop.',
  "Restart doesn't work on my laptop.",
  'Restart button does nothing on my laptop.',
  'Install page is blank on my computer.',
  'Update notes are cut off on my laptop.',
  'Launch screen is cut off on my laptop.',
  'Upgrade shop overlaps the HUD on my laptop.',
  'Also start screen is cut off on my laptop.',
  'Now restart button is gone on my laptop.',
  "Reload doesn't fix it on my laptop.",
  "On my laptop, restart doesn't work.",
  'Start and Restart overlap on my laptop.',
  'Cold start takes 10 seconds on my laptop.',
  'First launch on my laptop shows a blank screen.',
  'On first launch the start screen is blank on my laptop.',
  'Fresh install on my laptop shows a blank page.',
  'Start menu on my machine is too big.',
  'Start button is hidden on my laptop.',
  'Start page on this computer loads slowly.',
  'On this machine restart button does nothing.',
  'Update screen on my computer flickers.',
  'On my laptop install page is blank.',
  'Change log on my laptop is empty.',
  'Start and restart buttons overlap on my laptop.',
  'Start/restart buttons on my laptop overlap.',
  '"Start" button is hidden on my laptop.',
  // A subject, a modal, an aspect verb or a causative describes what a thing
  // does.
  'The enemies stop moving on my laptop after a minute.',
  'The sprites start flickering on my laptop.',
  'The colors change when I resize the window on my laptop.',
  'Buttons stop responding on my laptop.',
  'Things start to lag on my laptop after level 2.',
  'My laptop fans start spinning when the game is open.',
  "My laptop's fans start screaming on the boss level.",
  'The game will restart by itself on my laptop.',
  'It used to run fine on my laptop.',
  'It should run at 60fps on my laptop.',
  'Will the game run on my laptop?',
  'My laptop can barely run the game.',
  'My laptop struggles to run the boss level.',
  'It takes 10 seconds to start on my laptop.',
  "It doesn't seem to start on my laptop.",
  "The game won't even start on my laptop.",
  "The game won't let me restart on my laptop.",
  "There's no way to restart the level on my laptop.",
  'The game crashes and makes my laptop restart.',
  "Whenever you restart the level on my laptop the score doesn't reset.",
  'Some feedback on the elf game. The sprites start flickering on my laptop after level 2. Only work in Playground/elf-of-doom.',
  // A possessive, a modifier or another determiner makes the word a noun.
  "The game's restart is broken on my laptop.",
  "The elf's run animation stutters on my laptop.",
  "The elf's run speed is way too fast on my laptop.",
  "The player's start position is off-screen on my laptop.",
  'The green start text is cut off on my laptop.',
  'The blue restart prompt overlaps the score on my laptop.',
  "There's no restart option on my laptop.",
  'A hard reload on my laptop still shows the old start screen.',
  'Even after a hard reload on my laptop I see the old version.',
  'After a clean install on my laptop the start screen is blank.',
  'My second run on my laptop crashed at level 3.',
  'A test run on my laptop crashed at level 3.',
  'That change broke the layout on my laptop.',
  "Since yesterday's update the game won't load on my laptop.",
  // The owner describes an attempt, a press, or a game action they cannot do.
  'Tried to run it on my laptop and it crashed.',
  "I've tried to start it on my laptop twice and it freezes.",
  'I just tried to start it on my laptop and it froze.',
  'I was trying to run it on my laptop and it crashed.',
  'Clicking start on my laptop does nothing.',
  'Pressing restart on my laptop freezes the game.',
  'Clicking "Start" on my laptop does nothing.',
  'When I select Start on my laptop nothing happens.',
  "I can't restart the level on my laptop.",
  "I can't start the game on my laptop, the button does nothing.",
  "I can't change the volume on my laptop.",
  "We can't run the game on my laptop.",
  // An edit to the project, with the laptop only as where the owner looked.
  'Can you change the start screen so it fits on my laptop?',
  "Please update the layout so it isn't cut off on my laptop.",
  "Update the layout for my laptop's screen size.",
  'Can you make the game run better on my laptop?',
  'Please remove the extra scrollbar that shows on my laptop.',
  "Change the font size so it's readable on my laptop.",
  'Works on my laptop but not on my phone, can you change the layout?',
  'Stop the elf from clipping through the floor on my laptop.',
  'Remove the start menu, it covers the title on my laptop.',
  'Some feedback on the elf game. Can you change the start screen so it fits on my laptop? Only work in Playground/elf-of-doom.',
  // When a noun names something that froze or crashed the host, a later
  // action word in the clause describes the aftermath.
  'The start screen freezes my laptop and I need to restart it.',
  'The start screen froze my laptop and I had to force restart it.',
  'The start screen froze my laptop and had to restart.',
  'The start screen froze my laptop and I needed to restart.',
  "The start screen froze my laptop and I've had to restart twice.",
  'The start screen froze my laptop, forcing me to restart.',
  'The start screen freezes my laptop so you have to restart.',
  'The install page crashed my laptop, it had to restart.',
  'The restart button freezes my laptop until you stop the game.',
  'The update screen hangs my laptop and then I need to reload.',
  'The update screen freezes my laptop when trying to change settings.',
  'The restart button lags my laptop whenever the enemies start moving.',
  // A request for help is project feedback unless the clause names host
  // software. "ODS" in the name of a link does not count.
  `${SPARK_SENTENCE.slice(0, -1)}, please fix it.`,
  `${SPARK_SENTENCE} Can you fix it?`,
  "The game won't start on my laptop, can you fix it?",
  "Docker is fine and the game won't start on my laptop, please fix it.",
  'The start screen is cut off on my laptop, please redo it.',
]) {
  test(`describing the owner's project on a host is not a host command: ${text}`, () => {
    assert.equal(api.userMessageRequestsHostCommand([], text), false);
  });
}

for (const text of [
  'Start the docker service on my laptop.',
  "Restart my machine's ODS services.",
  'Update ODS on this machine.',
  'Restart Docker on my laptop.',
  'Run `df -h` on my laptop.',
  'Install htop on this machine.',
  'I want to restart Docker on my laptop.',
  // A leading host phrase still names the target of the request.
  'On my laptop, please restart Docker.',
  'On this ODS host, go ahead and restart Docker.',
  'On my laptop, just restart Docker.',
  'On my laptop I need you to restart Docker.',
  'On my machine, sudo apt install htop.',
  'In my laptop terminal run df -h.',
  // A UI noun after a word that opens the request is its object.
  'Delete log files on my laptop.',
  'Change screen resolution on my laptop.',
  'Start screen recording on my machine.',
  'Can you delete log files on my laptop?',
  // The owner could not do it and asks Pixel to.
  "I can't restart Docker on my laptop, please do it for me.",
  "I couldn't install htop on my machine, can you do it?",
  // A workspace scope elsewhere in the message does not hide the request.
  'Only work in Playground/elf-of-doom. Restart Docker on my laptop.',
  'Only work in Playground/elf-of-doom. Restart Docker on this ODS host.',
  'Restart Docker on my laptop. Then only work in Playground/elf-of-doom for the game fix.',
  "You don't have to only work in the sandbox. Restart Docker on my laptop.",
  // Describing a host problem and then asking Pixel to fix it.
  "Docker won't start on my laptop, can you fix it?",
  "Ollama doesn't start on my machine anymore, please fix it.",
  "On my laptop Docker won't start anymore, can you fix it?",
  'ODS fails to start on my laptop, please take a look and fix it.',
  'Docker refuses to start on my ODS host, can you fix that?',
  "If Docker doesn't start on my laptop, reinstall it.",
  "Ollama won't run on my laptop, please reinstall it.",
  "My laptop can't run Docker anymore, please fix it.",
  "ODS won't update on my laptop, can you force it?",
  "ODS didn't update on my laptop, please try again.",
  "The update won't install on my laptop, please do it manually.",
  'The last update broke Ollama on my machine. Can you roll it back?',
  'The latest update broke Docker on my laptop, can you roll it back?',
  'The update broke ODS on my laptop, please undo it.',
  'After the update ODS is broken on my laptop, please fix it.',
  'The install on my laptop got interrupted, please redo it.',
  "The elf game is fun. Separately, Docker won't start on my laptop, can you fix it?",
  // An action noun that is the object of a request verb, or that the host needs.
  'Please apply the latest update on my laptop.',
  'Can you do the update on my ODS host?',
  'Finish the update on my ODS host.',
  'Retry the update on my laptop.',
  'Please continue the update on my laptop.',
  'Please resume the install on my laptop.',
  'Please perform the upgrade on my ODS host.',
  'Can you handle the update on my laptop?',
  'Go ahead with the upgrade on my laptop.',
  'Kick off an install of CUDA on my machine.',
  'Trigger a restart of the ODS services on my machine.',
  'I need a restart of Docker on my laptop.',
  'My ODS host needs a restart, please do it.',
  'This machine needs a restart, go ahead.',
  'Docker on this machine needs a restart.',
  'My laptop needs an update, can you do it?',
  'The ODS host needs an update, go ahead.',
  'I started the update on my laptop but it stalled, please finish it.',
  'Give Docker a restart on my machine.',
  'My ODS host has a pending update, apply it.',
  // The owner tried or cannot do it and hands it over, or proposes it with "we".
  'I tried to install CUDA on my laptop but it failed, can you do it for me?',
  'I tried to restart Docker on my laptop but it hangs, please do it.',
  'I tried to update ODS on my laptop and it got stuck, please finish it.',
  'I try to start Docker on my laptop but it errors, can you do it?',
  'I failed to update the driver on my machine, can you do it?',
  'I have to restart Docker on my laptop every morning, can you do it now?',
  "I still can't restart Docker on my laptop, please do it.",
  "I really can't restart Docker on my laptop, please help.",
  "I just can't install htop on my machine, can you?",
  "I still can't update ODS on my laptop, can you do it?",
  "We still can't start Ollama on my laptop, please handle it.",
  "I can't restart Docker on my laptop.",
  'Can we stop Ollama on my machine?',
  'Can we start Ollama on my laptop?',
  'Can we remove the old models on my machine to free space?',
  'Could we delete the cached images on my laptop?',
  'Can we update ODS on my ODS host tonight?',
  'Shall we install htop on this machine?',
  'Shall we upgrade ODS on this machine?',
  'So we stop Docker on my laptop first, ok?',
  // An opener, a vocative or a leading host phrase before a request about logs
  // or a screen setting.
  'Yes, delete logs on my machine.',
  'Ok, delete log files on my laptop.',
  'Go ahead and delete log files on my machine.',
  'Please go ahead and delete log files on my laptop.',
  'Help me delete logs on my laptop.',
  'Pixel, delete logs on my ODS host to free some space.',
  'On my laptop, delete logs older than 7 days.',
  'On my ODS host, delete log files from last month.',
  'On this machine, remove log files to free space.',
  'Before you do anything else, delete logs on my ODS host.',
  'And delete logs on my laptop too.',
  'Ok so remove logs from my laptop.',
  'Could you clear and delete logs on my laptop?',
  'Great. Also clean up and delete log files on my laptop.',
  'Pixel, run log rotation on my machine.',
  'Pixel please run log cleanup on my laptop.',
  'Alright, remove log files older than a week on this machine.',
  'Hey, change screen resolution on my laptop.',
  'On my laptop, change screen timeout to 30 minutes.',
  // "this" as the object of make, and an aspect verb that opens a request.
  'Can you make this run on my laptop?',
  'Make this start on boot on my machine.',
  'Stop running containers on my laptop.',
]) {
  test(`an action verb aimed at the host is still a host command: ${text}`, () => {
    assert.equal(api.userMessageRequestsHostCommand([], text), true);
    assert.deepEqual(api.userMessageOperationsRequirements([], text),
      {required: true, actions: ['raw-shell']});
  });
}

for (const prompt of [
  SPARK_SENTENCE,
  SPARK_FEEDBACK,
  'When I restart the level on my machine it crashes.',
  'The start screen froze my laptop and I had to restart.',
  'Start screen is cut off on my laptop.',
  "I can't restart the level on my laptop.",
  'The start screen freezes my laptop and I need to restart it.',
  'Some feedback on the elf game. The sprites start flickering on my laptop after level 2. Only work in Playground/elf-of-doom.',
  'Some feedback on the elf game. Can you change the start screen so it fits on my laptop? Only work in Playground/elf-of-doom.',
]) {
  test(`game feedback keeps workspace tools: ${prompt}`, () => {
    const guard = observe(prompt);
    const read = call(guard, 'read', {path: 'Playground/elf-of-doom/index.html'});
    assert.notEqual(read?.block, true, read?.blockReason);
    const exec = call(guard, 'exec', {command: 'ls Playground/elf-of-doom'});
    assert.notEqual(exec?.block, true, exec?.blockReason);
  });
}

for (const prompt of [
  'On this ODS host, go ahead and restart Docker.',
  'Delete log files on my laptop.',
  "Docker won't start on my laptop, can you fix it?",
  'Please apply the latest update on my laptop.',
  "I still can't restart Docker on my laptop, please do it.",
  'Yes, delete logs on my machine.',
]) {
  test(`a host request goes through the approval proposal, not generic exec: ${prompt}`, () => {
    const guard = observe(prompt);
    assert.deepEqual(call(guard, 'exec', {command: 'sudo systemctl restart docker'}), {
      block: true,
      blockReason: api.OPERATIONS_HOST_COMMAND_REQUIRES_PROPOSAL_REASON,
    });
    const proposal = call(guard, 'pixel_ods_host_command_propose', {command: 'sudo systemctl restart docker'});
    assert.notEqual(proposal?.block, true, proposal?.blockReason);
  });
}
