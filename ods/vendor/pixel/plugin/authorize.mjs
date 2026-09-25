import { createServer } from "node:http";
import { createHash, randomBytes } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";
import { installedGoogleClient, readPrivateJson, writePrivateJson } from "./oauth-security.mjs";

const tokenPath = process.env.PIXEL_GOOGLE_TOKEN_PATH ?? join(homedir(), ".config", "pixel-google-workspace", "token.json");
const clientPath = process.env.PIXEL_GOOGLE_CLIENT_FILE ?? join(homedir(), ".config", "pixel-google-workspace", "client.json");
const account = process.env.PIXEL_GOOGLE_ACCOUNT ?? "";
const redirectUri = process.env.PIXEL_OAUTH_REDIRECT_URI ?? "http://127.0.0.1:8765";
const enabled = (name, fallback) => !["0", "false", "off", "no"].includes(String(process.env[name] ?? fallback).toLowerCase());
const scopes = [];
if (enabled("PIXEL_LIMB_EMAIL_ENABLED", "1")) scopes.push("https://www.googleapis.com/auth/gmail.readonly");
if (enabled("PIXEL_LIMB_CALENDAR_ENABLED", "1")) scopes.push("https://www.googleapis.com/auth/calendar.events");
if (scopes.length === 0) throw new Error("Google authorization is unnecessary because the email and Calendar limbs are disabled");

let source;
try { source = await readPrivateJson(clientPath); }
catch (error) {
  if (error?.code !== "ENOENT") throw error;
  source = await readPrivateJson(tokenPath);
}
const client = installedGoogleClient(source);

const callbackUrl = new URL(redirectUri);
if (callbackUrl.hostname !== "127.0.0.1" && callbackUrl.hostname !== "localhost") {
  throw new Error("PIXEL_OAUTH_REDIRECT_URI must use a loopback host");
}
const state = randomBytes(24).toString("hex");
const codeVerifier = randomBytes(48).toString("base64url");
const codeChallenge = createHash("sha256").update(codeVerifier).digest("base64url");
const authUrl = new URL(client.auth_uri);
authUrl.search = new URLSearchParams({
  client_id: client.client_id, redirect_uri: redirectUri, response_type: "code",
  scope: scopes.join(" "), access_type: "offline", prompt: "consent",
  include_granted_scopes: "false", login_hint: account, state,
  code_challenge: codeChallenge, code_challenge_method: "S256",
}).toString();
console.log(`Open this URL in the account owner's browser:\n${authUrl}`);

const server = createServer(async (request, response) => {
  try {
    const callback = new URL(request.url, redirectUri);
    if (callback.pathname !== callbackUrl.pathname) return response.writeHead(404).end("Not found");
    if (callback.searchParams.get("state") !== state) return response.writeHead(400).end("Invalid OAuth state");
    const code = callback.searchParams.get("code");
    if (!code || callback.searchParams.get("error")) throw new Error(callback.searchParams.get("error") ?? "Missing authorization code");
    const tokenResponse = await fetch(client.token_uri, {
      method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ code, code_verifier: codeVerifier, client_id: client.client_id, client_secret: client.client_secret, redirect_uri: redirectUri, grant_type: "authorization_code" }),
      signal: AbortSignal.timeout(20000),
    });
    const token = await tokenResponse.json();
    if (!tokenResponse.ok || !token.refresh_token) throw new Error(`Token exchange failed (${tokenResponse.status})`);
    await writePrivateJson(tokenPath, {
      client_id: client.client_id, client_secret: client.client_secret,
      refresh_token: token.refresh_token, token_uri: client.token_uri,
      scope: scopes.join(" "), email: account,
    });
    response.writeHead(200, { "content-type": "text/plain; charset=utf-8" }).end("Pixel authorization complete. You may close this tab.");
    console.log("Authorization complete; token stored outside the repository.");
  } catch (error) {
    response.writeHead(500, { "content-type": "text/plain; charset=utf-8" }).end("Authorization failed. Check the terminal.");
    console.error("OAuth authorization failed. Verify the private client configuration and retry.");
  } finally { setTimeout(() => server.close(), 250); }
});
server.listen(Number(callbackUrl.port || 80), callbackUrl.hostname);
