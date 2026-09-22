const status = document.querySelector('#status');
const host = document.querySelector('#document');
let generation = 0;
function render(specification, id) {
  if (id !== generation) return;
  const target = document.createElement('div');
  host.replaceChildren(target);
  status.textContent = 'Loading documentation…';
  try {
    Redoc.init(specification, { sanitize: true, theme: { typography: { fontFamily: 'system-ui, sans-serif', headings: { fontFamily: 'system-ui, sans-serif' } } } }, target, error => {
      if (id !== generation) return;
      status.textContent = error ? 'Unable to read this specification. Check its format, references and server CORS settings.' : 'Documentation loaded.';
    });
  } catch {
    if (id === generation) status.textContent = 'Unable to initialize the documentation reader.';
  }
}
document.querySelector('#source').addEventListener('submit', event => {
  event.preventDefault();
  const id = ++generation;
  try {
    const url = new URL(document.querySelector('#url').value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error();
    render(url.href, id);
  } catch { status.textContent = 'Use an HTTP or HTTPS URL without embedded credentials.'; }
});
document.querySelector('#file').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  const id = ++generation;
  if (file.size > 10 * 1024 * 1024) { status.textContent = 'Choose a JSON file no larger than 10 MiB.'; return; }
  try {
    const specification = JSON.parse(await file.text());
    if (!specification || typeof specification !== 'object' || Array.isArray(specification) || !(specification.openapi || specification.swagger)) throw new Error();
    render(specification, id);
  } catch { if (id === generation) status.textContent = 'Choose a valid OpenAPI or Swagger JSON document.'; }
});
