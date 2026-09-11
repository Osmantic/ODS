import { useState, useRef, useEffect, useCallback } from 'react';
import { readSettings, prepareSettingsSave, GROUPS, CONTROLS } from './pixelRuntimeSettingsForm';
import PixelSettingsRuntime from './PixelSettingsRuntime';

function scalarToString(value) {
  if (value === null || value === undefined) return '';
  return String(value);
}

function controlType(controlName) {
  return CONTROLS[controlName]?.type;
}

function controlChoices(controlName) {
  return CONTROLS[controlName]?.choices;
}

function isInteger(controlName) {
  return CONTROLS[controlName]?.type === 'integer';
}

function buildSelectOptions(choices) {
  return (
    <>
      <option value="">Automatic</option>
      {choices.map((c) => (
        <option key={c} value={c}>{c}</option>
      ))}
    </>
  );
}

function buildBooleanOptions() {
  return (
    <>
      <option value="">Automatic</option>
      <option value="true">Yes</option>
      <option value="false">No</option>
    </>
  );
}

export default function PixelRuntimeSettings() {
  const [snapshot, setSnapshot] = useState(null);
  const [rawChanges, setRawChanges] = useState({});
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [stale, setStale] = useState(false);
  const [pending, setPending] = useState(false);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const runtimeBusyRef = useRef(false);
  const runtimeBusyChanged = useCallback((value) => {
    if (value && pendingRef.current) return false;
    runtimeBusyRef.current = value;
    setRuntimeBusy(value);
    return true;
  }, []);

  const mountedRef = useRef(true);
  const seqRef = useRef(0);
  const abortRef = useRef(null);
  const pendingRef = useRef(false);

  const clearAbort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  const doRequest = useCallback((seq, fn) => {
    if (!mountedRef.current || seq !== seqRef.current) return false;
    return fn();
  }, []);

  const loadSettings = useCallback(() => {
    if (pendingRef.current || runtimeBusyRef.current) return;
    const seq = seqRef.current + 1;
    seqRef.current = seq;
    setPending(true);
    pendingRef.current = true;
    setError(null);
    setNotice(null);
    clearAbort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const timer = setTimeout(() => {
      ctrl.abort();
    }, 10000);
    fetch('/api/pixel/settings', { method: 'GET', signal: ctrl.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        clearTimeout(timer);
        doRequest(seq, () => {
          const snap = readSettings(data);
          setSnapshot(snap);
          setRawChanges({});
          setStale(false);
        });
      })
      .catch((e) => {
        clearTimeout(timer);
        doRequest(seq, () => {
          setStale(true);
          setError('Settings unavailable: ' + e.message);
        });
      })
      .finally(() => {
        doRequest(seq, () => {
          abortRef.current = null;
          setPending(false);
          pendingRef.current = false;
        });
      });
  }, [clearAbort, doRequest]);

  useEffect(() => {
    mountedRef.current = true;
    loadSettings();
    return () => {
      mountedRef.current = false;
      seqRef.current += 1;
      pendingRef.current = false;
      clearAbort();
    };
  }, [loadSettings, clearAbort]);

  const hasChanges = Object.keys(rawChanges).length > 0;
  const canSave = snapshot && hasChanges && !stale && !pending && !runtimeBusy && snapshot.revision < Number.MAX_SAFE_INTEGER;
  const canReload = !pending && !runtimeBusy;
  const canCancel = hasChanges && !pending && !runtimeBusy;

  const handleSave = useCallback(() => {
    if (pendingRef.current || runtimeBusyRef.current || !canSave) return;
    const seq = seqRef.current + 1;
    seqRef.current = seq;
    setPending(true);
    pendingRef.current = true;
    setError(null);
    setNotice(null);
    clearAbort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), 10000);
    let body;
    try {
      body = prepareSettingsSave(snapshot, rawChanges);
    } catch (e) {
      clearTimeout(timer);
      setError('Validation error: ' + e.message);
      setPending(false);
      pendingRef.current = false;
      abortRef.current = null;
      return;
    }
    fetch('/api/pixel/settings/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        clearTimeout(timer);
        doRequest(seq, () => {
          const snap = readSettings(data, snapshot.revision + 1);
          for (const [k, v] of Object.entries(body.changes)) {
            if (!Object.hasOwn(snap.preferences, k)) throw new Error(`Key ${k} missing after save`);
            if (snap.preferences[k] !== v) throw new Error(`Value mismatch for ${k}`);
          }
          setSnapshot(snap);
          setRawChanges({});
          setStale(false);
          setError(null);
          setNotice('Preferences saved. Pixel runtime is unchanged.');
        });
      })
      .catch((e) => {
        clearTimeout(timer);
        doRequest(seq, () => {
          setStale(true);
          setError('Save failed: ' + e.message);
        });
      })
      .finally(() => {
        doRequest(seq, () => {
          abortRef.current = null;
          setPending(false);
          pendingRef.current = false;
        });
      });
  }, [canSave, snapshot, rawChanges, clearAbort, doRequest]);

  const handleReload = useCallback(() => {
    if (pendingRef.current || runtimeBusyRef.current || pending) return;
    if (hasChanges) {
      if (!window.confirm('Discard unsaved edits and reload?')) return;
    }
    loadSettings();
  }, [pending, hasChanges, loadSettings]);

  const handleCancel = useCallback(() => {
    if (pendingRef.current || runtimeBusyRef.current || pending) return;
    setRawChanges({});
    setError(null);
    setNotice(null);
  }, [pending]);

  const handleChange = useCallback((controlName, value) => {
    if (pendingRef.current || runtimeBusyRef.current) return;
    setNotice(null);
    setRawChanges((prev) => ({ ...prev, [controlName]: value }));
  }, []);

  const displayValue = (controlName) => {
    if (rawChanges[controlName] !== undefined) return rawChanges[controlName];
    if (snapshot && snapshot.preferences[controlName] !== undefined) {
      return scalarToString(snapshot.preferences[controlName]);
    }
    return '';
  };

  const renderField = (controlName, group) => {
    const ct = controlType(controlName);
    const label = group.labels[controlName] || controlName;
    const fieldId = `field-${controlName}`;
    const helpId = `help-${controlName}`;
    const hasHelp = group.help && group.help[controlName];
    const value = displayValue(controlName);

    let inputEl;
    if (ct === 'boolean') {
      inputEl = (
        <select
          id={fieldId}
          className="w-full min-w-0 rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text"
          disabled={pending || runtimeBusy || !snapshot}
          value={value}
          onChange={(e) => handleChange(controlName, e.target.value)}
          aria-describedby={hasHelp ? helpId : undefined}
        >
          {buildBooleanOptions()}
        </select>
      );
    } else if (ct === 'enum') {
      inputEl = (
        <select
          id={fieldId}
          className="w-full min-w-0 rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text"
          disabled={pending || runtimeBusy || !snapshot}
          value={value}
          onChange={(e) => handleChange(controlName, e.target.value)}
          aria-describedby={hasHelp ? helpId : undefined}
        >
          {buildSelectOptions(controlChoices(controlName))}
        </select>
      );
    } else {
      inputEl = (
        <input
          type="number"
          id={fieldId}
          className="w-full min-w-0 rounded border border-theme-border bg-theme-bg px-3 py-2 text-theme-text"
          disabled={pending || runtimeBusy || !snapshot}
          value={value}
          min={CONTROLS[controlName].min}
          max={CONTROLS[controlName].max}
          placeholder="Automatic"
          step={isInteger(controlName) ? 1 : 'any'}
          onChange={(e) => handleChange(controlName, e.target.validity.badInput ? 'invalid-number' : e.target.value)}
          aria-describedby={hasHelp ? helpId : undefined}
        />
      );
    }

    return (
      <div key={controlName} className="min-w-0">
        <label htmlFor={fieldId} className="block text-sm font-medium text-theme-text">{label}</label>
        {inputEl}
        {hasHelp && (
          <p id={helpId} className="mt-1 text-xs text-theme-text-muted">{group.help[controlName]}</p>
        )}
      </div>
    );
  };

  return (
    <section aria-labelledby="pixel-runtime-title" className="rounded-lg border border-theme-border bg-theme-card p-5 text-theme-text space-y-4 min-w-0">
      <h2 id="pixel-runtime-title" className="text-lg font-semibold">Pixel runtime settings</h2>
      <p className="text-sm text-theme-text-muted">Saving preferences does not apply them to Pixel.</p>
      <p className="text-sm text-theme-text-muted">Inspect runtime support below, then apply your saved preferences when Pixel is idle.</p>
      {stale && (
        <div className="text-sm text-amber-600">Connection stale. Reload before saving.</div>
      )}
      {error && <div role="alert" className="text-sm text-red-600">{error}</div>}
      {notice && <div role="status" className="text-sm text-green-600">{notice}</div>}
      {snapshot && GROUPS.map((group) => (
        <fieldset key={group.id} className="min-w-0 border border-theme-border rounded-lg p-4 space-y-4">
          <legend className="text-sm font-medium text-theme-text">{group.label}</legend>
          <div className="grid min-w-0 grid-cols-1 md:grid-cols-2 gap-4">
            {group.controls.map((c) => renderField(c, group))}
          </div>
        </fieldset>
      ))}
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40"
          disabled={!canSave}
          onClick={handleSave}
          aria-label="Save Pixel preferences"
        >
          {pending ? 'Please wait…' : 'Save Pixel preferences'}
        </button>
        <button
          className="rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40"
          disabled={!canReload}
          onClick={handleReload}
        >
          Reload Pixel preferences
        </button>
        <button
          className="rounded border border-theme-border px-3 py-2 text-sm disabled:opacity-40"
          disabled={!canCancel}
          onClick={handleCancel}
        >
          Cancel Pixel edits
        </button>
      </div>
      <PixelSettingsRuntime savedRevision={snapshot?.revision ?? null} saving={pending}
        blocked={hasChanges || stale || !snapshot} onBusyChange={runtimeBusyChanged} />
    </section>
  );
}
