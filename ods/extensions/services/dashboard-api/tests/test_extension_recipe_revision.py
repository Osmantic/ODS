import json
import pytest

import extension_recipe_revision as revision
from extension_installation import InstallationJournal


@pytest.fixture
def recipe(tmp_path):
    directory = tmp_path / 'extension'; directory.mkdir()
    (directory / 'manifest.yaml').write_bytes(b'old manifest')
    (directory / 'compose.yaml').write_bytes(b'old compose')
    (directory / 'data').mkdir()
    (directory / 'data/database').write_bytes(b'user data')
    (directory / '.env').write_bytes(b'private settings')
    return tmp_path / 'revision.json', {'user': directory}, {'user': {
        'manifest.yaml': b'new manifest', 'compose.yaml': b'new compose'}}


def test_revision_retains_data_settings_and_supports_rollback(recipe):
    journal, directories, replacements = recipe
    revision.stage_revision(journal, directories, replacements)
    assert (directories['user'] / 'manifest.yaml').read_bytes() == b'old manifest'
    for _ in range(2): revision.recover_revision(journal, directories)
    assert (directories['user'] / 'compose.yaml').read_bytes() == b'new compose'
    assert (directories['user'] / 'data/database').read_bytes() == b'user data'
    assert (directories['user'] / '.env').read_bytes() == b'private settings'
    for _ in range(2): revision.recover_revision(journal, directories, rollback=True)
    assert (directories['user'] / 'compose.yaml').read_bytes() == b'old compose'
    assert journal.exists()


@pytest.mark.parametrize('condition', ['running', 'uncertain', 'wrong-operation', 'changed-request'])
def test_bound_revision_refuses_unverified_attempt_before_changing_files(recipe, condition):
    file_journal, directories, replacements = recipe
    revision.stage_revision(file_journal, directories, replacements)
    attempts = InstallationJournal(file_journal.parent / 'attempts.json')
    expected = {'action':'install', 'state':'accepted', 'operationId':'a'*32}
    attempts.records['example'] = expected.copy(); attempts.save()
    old = {'extensionId':'example', 'draftId':'a'*64, 'recipeDigest':'b'*64}
    new = {**old, 'draftId':'c'*64, 'recipeDigest':'d'*64}
    observed = {'service_id':'example', 'operation_id':'a'*32, 'state':'failed'}
    if condition in ('running', 'uncertain'): observed['state'] = condition
    if condition == 'wrong-operation': observed['operation_id'] = 'f'*32
    with pytest.raises(ValueError):
        revision.commit_bound_revision(file_journal, directories, attempts, 'example', expected,
            old, new, lambda: {} if condition == 'changed-request' else old,
            lambda: pytest.fail('must not bind'), lambda *args: observed)
    assert (directories['user'] / 'manifest.yaml').read_bytes() == b'old manifest'
    assert InstallationJournal(attempts.path).records['example'] == expected
    assert (directories['user'] / '.env').read_bytes() == b'private settings'


def test_interrupted_file_replacement_recovers_from_durable_journal(recipe, monkeypatch):
    journal, directories, replacements = recipe
    revision.stage_revision(journal, directories, replacements)
    atomic = revision._atomic
    def interrupted(path, content):
        if path.name == 'compose.yaml': raise OSError('simulated crash')
        atomic(path, content)
    monkeypatch.setattr(revision, '_atomic', interrupted)
    with pytest.raises(OSError): revision.recover_revision(journal, directories)
    assert (directories['user'] / 'manifest.yaml').read_bytes() == b'new manifest'
    assert (directories['user'] / 'compose.yaml').read_bytes() == b'old compose'
    monkeypatch.setattr(revision, '_atomic', atomic)
    revision.recover_revision(journal, directories)
    assert (directories['user'] / 'compose.yaml').read_bytes() == b'new compose'


def test_local_edit_blocks_whole_revision_without_overwriting_it(recipe):
    journal, directories, replacements = recipe
    revision.stage_revision(journal, directories, replacements)
    (directories['user'] / 'compose.yaml').write_bytes(b'user edit')
    with pytest.raises(ValueError): revision.recover_revision(journal, directories)
    assert (directories['user'] / 'manifest.yaml').read_bytes() == b'old manifest'
    assert (directories['user'] / 'compose.yaml').read_bytes() == b'user edit'


@pytest.mark.parametrize('name', ['../outside', '.env', 'data/database'])
def test_only_recipe_files_can_be_replaced(recipe, name):
    journal, directories, _ = recipe
    with pytest.raises(ValueError): revision.stage_revision(journal, directories, {'user': {name:b'new'}})
    assert not journal.exists()


def test_symlink_and_duplicate_journal_entries_are_rejected(recipe):
    journal, directories, replacements = recipe
    revision.stage_revision(journal, directories, replacements)
    record = json.loads(journal.read_text())
    record['entries'].append(record['entries'][0])
    journal.write_text(json.dumps(record))
    with pytest.raises(ValueError): revision.recover_revision(journal, directories)
    path = directories['user'] / 'manifest.yaml'
    path.unlink(); path.symlink_to(directories['user'] / '.env')
    with pytest.raises(ValueError): revision.recover_revision(journal, directories)
    assert (directories['user'] / '.env').read_bytes() == b'private settings'


@pytest.mark.parametrize('failure', ['none', 'before-binding', 'after-binding', 'retirement'])
def test_bound_revision_recovers_each_commit_boundary(recipe, monkeypatch, failure):
    file_journal, directories, replacements = recipe
    revision.stage_revision(file_journal, directories, replacements)
    attempts = InstallationJournal(file_journal.parent / 'attempts.json')
    expected = {'action':'install', 'state':'accepted', 'operationId':'a'*32}
    attempts.records['example'] = expected.copy(); attempts.save()
    old = {'extensionId':'example', 'draftId':'a'*64, 'recipeDigest':'b'*64}
    new = {**old, 'draftId':'c'*64, 'recipeDigest':'d'*64}
    state = {'binding': old}
    def bind():
        if failure == 'before-binding': raise OSError('binding unavailable')
        state['binding'] = new
        if failure == 'after-binding': raise OSError('reply lost')
    def observe(*args):
        return {'service_id':'example', 'operation_id':'a'*32, 'state':'failed'}
    save = attempts.save
    if failure == 'retirement':
        monkeypatch.setattr(attempts, 'save', lambda: (_ for _ in ()).throw(OSError('journal unavailable')))
    def commit():
        return revision.commit_bound_revision(file_journal, directories, attempts, 'example', expected,
            old, new, lambda: state['binding'], bind, observe)
    if failure != 'none':
        with pytest.raises(OSError): commit()
        assert attempts.records['example'] == expected
        assert (directories['user'] / 'compose.yaml').read_bytes() == (
            b'old compose' if failure == 'before-binding' else b'new compose')
    monkeypatch.setattr(attempts, 'save', save)
    failure = 'none'
    assert commit() == new
    assert state['binding'] == new
    assert InstallationJournal(attempts.path).records == {}
    assert commit() == new  # Lost final response is idempotent.
    assert (directories['user'] / 'data/database').read_bytes() == b'user data'
