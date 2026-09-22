// Native runtime settings; the administrator controls executable flow content.
function required(name) {
    const value = process.env[name];
    if (!value) throw new Error(`${name} is required`);
    return value;
}

function passwordHash(name) {
    const value = required(name);
    if (!/^\$2[aby]\$(?:0[8-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}$/.test(value)) {
        throw new Error(`${name} must be a complete bcrypt hash with cost at least 8`);
    }
    return value;
}

module.exports = {
    uiHost: "0.0.0.0",
    uiPort: 1880,
    userDir: "/data",
    flowFile: "flows.json",
    flowFilePretty: true,
    credentialSecret: required("NODERED_CREDENTIAL_SECRET"),
    httpAdminRoot: "/admin",
    httpNodeRoot: "/flows",
    adminAuth: {
        type: "credentials",
        users: [{ username: "ods", password: passwordHash("NODERED_ADMIN_HASH"), permissions: "*" }],
        sessionExpiryTime: 3600,
    },
    httpNodeAuth: { user: "workflow", pass: passwordHash("NODERED_HTTP_HASH") },
    apiMaxLength: "1mb",
    telemetry: { enabled: false },
    diagnostics: { enabled: false, ui: false },
    logging: { console: { level: "info", metrics: false, audit: false } },
    contextStorage: { default: { module: "localfilesystem" } },
    externalModules: {
        autoInstall: false,
        palette: { allowInstall: false, allowUpdate: false, allowUpload: false },
        modules: { allowInstall: false },
    },
    functionExternalModules: false,
    functionTimeout: 10,
    globalFunctionTimeout: 10,
    nodeMessageBufferMaxLength: 1000,
    httpRequestTimeout: 30000,
    editorTheme: { projects: { enabled: false } },
};
