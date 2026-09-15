const http = require("node:http");
const request = http.get("http://127.0.0.1:1880/admin/auth/login", { timeout: 4000 }, (response) => {
    response.resume();
    process.exitCode = response.statusCode === 200 ? 0 : 1;
});
request.on("timeout", () => request.destroy(new Error("Health request timed out")));
request.on("error", (error) => {
    console.error(error.message);
    process.exitCode = 1;
});
