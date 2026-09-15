// Bootstrap before HTTP can expose native first-superuser onboarding.
onBootstrap((event) => {
  event.next();
  const hasBootstrap = $os.readDir("/tmp").some((entry) => entry.name() === "pocketbase-bootstrap-password");
  event.app.runInTransaction((app) => {
    if (app.findAllRecords("_superusers").length > 0) {
      return;
    }
    if ($os.getenv("ODS_POCKETBASE_FRESH") !== "1" || !hasBootstrap) {
      throw new Error("PocketBase has no superuser; recover the account before startup");
    }
    const admin = new Record(app.findCollectionByNameOrId("_superusers"));
    admin.set("email", "ods@localhost.invalid");
    admin.setPassword(toString($os.readFile("/tmp/pocketbase-bootstrap-password")));
    app.save(admin);
    const settings = app.settings();
    settings.meta.appName = "ODS PocketBase";
    settings.meta.appURL = "http://localhost:" + ($os.getenv("POCKETBASE_PORT") || "8090");
    settings.logs.logIP = false;
    app.save(settings);
  });
  if (hasBootstrap) {
    $os.remove("/tmp/pocketbase-bootstrap-password");
  }
});
