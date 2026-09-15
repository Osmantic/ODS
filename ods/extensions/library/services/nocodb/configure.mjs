// Preserve arbitrary database-password punctuation at the native Knex boundary.
const password = process.env.NOCODB_DB_PASSWORD;
if (!password) throw new Error("NOCODB_DB_PASSWORD is required");
process.env.NC_DB_JSON = JSON.stringify({
  client: "pg",
  connection: { host: "nocodb-db", port: 5432, user: "nocodb", database: "nocodb", password },
});
