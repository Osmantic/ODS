// Portal's own one-line notes about a reply's preview: what its checks could
// not confirm or finish, a page that broke while it tested it, an earlier
// version, a step that has not started, or a preview it could not publish or
// a build that stopped first. Each is a paragraph of its own after the reply,
// shown muted and never as an alert. The pixel-agent plugin writes them
// (plugin/preview-explanation.mjs and tool-loop-guard.mjs); its tests keep
// this list equal to the plugin's.
export const DELIVERY_NOTES = Object.freeze([
  "Portal couldn't test every control automatically. Try it, and tell Portal if something isn't right.",
  "Portal couldn't confirm everything automatically. Have a look, and tell Portal if something isn't right.",
  "Portal's checks were still running when it replied. Have a look, and tell Portal if something isn't right.",
  'The page ran into a script problem while Portal was testing it, so parts of it may not work yet. Ask Portal to fix it.',
  "The page doesn't match everything you asked for yet. Ask Portal to fix it.",
  "This preview shows the version from before Portal's latest changes. Ask Portal to publish the current files.",
  'Portal stopped before it finished, so this preview shows an earlier version. Send a message to continue.',
  "Portal published your page but couldn't finish checking it automatically. Try it, and tell Portal if something isn't right.",
  'Portal stopped before it finished, so this page may not have everything you asked for yet. Send a message to continue.',
  "The next step above hasn't started yet. Send a message to continue.",
  "Portal saved the website files in your workspace but couldn't publish a preview of them, so they haven't been checked and there is no preview to open yet. Ask Portal to publish the preview.",
  "Portal didn't create the website files for this request, so there is no preview to open yet. Ask Portal to try the build again.",
  "Portal stopped before it finished because it wasn't making progress.",
])
