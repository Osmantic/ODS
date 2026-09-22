"use strict";
const statusNode = document.getElementById("status");
let selection = 0;
function status(message, error = false) {
  statusNode.textContent = message;
  statusNode.dataset.error = String(error);
}
function openDocument(source) {
  document.getElementById("swagger-ui").replaceChildren();
  window.ui = SwaggerUIBundle({
    ...source,
    dom_id: "#swagger-ui",
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
    layout: "BaseLayout",
    validatorUrl: null,
    queryConfigEnabled: false,
    persistAuthorization: false,
    tryItOutEnabled: false,
    deepLinking: false
  });
}
document.getElementById("open-url").addEventListener("submit", (event) => {
  event.preventDefault();
  selection++;
  try {
    const url = new URL(document.getElementById("spec-url").value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("Use an HTTP(S) URL without embedded credentials.");
    }
    openDocument({url: url.href});
    status("Loading the selected URL. The API must allow browser access; loading errors appear below.");
  } catch (error) { status(error.message, true); }
});
document.getElementById("spec-file").addEventListener("change", async (event) => {
  const current = ++selection;
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error("Choose a JSON document smaller than 10 MiB.");
    const spec = JSON.parse(await file.text());
    if (current !== selection) return;
    if (!spec || typeof spec !== "object" || (!spec.openapi && !spec.swagger)) {
      throw new Error("This JSON does not declare an OpenAPI or Swagger version.");
    }
    openDocument({spec});
    status(`Opened ${file.name}. For relative references, serve the specification from its original URL.`);
  } catch (error) { if (current === selection) status(error.message, true); }
});
