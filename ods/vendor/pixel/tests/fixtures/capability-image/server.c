#include <stdio.h>
#include <string.h>

int main(void) {
  char buffer[4096];
  while (fgets(buffer, sizeof buffer, stdin) != NULL) {
    if (strstr(buffer, "\"method\":\"server/discover\"") != NULL) {
      fputs("{\"jsonrpc\":\"2.0\",\"id\":\"pixel-discover\",\"result\":{\"resultType\":\"complete\",\"supportedVersions\":[\"2026-07-28\"],\"capabilities\":{\"tools\":{}},\"_meta\":{\"io.modelcontextprotocol/serverInfo\":{\"name\":\"live-fixture-mcp\",\"version\":\"1.0.0\"}}}}\n", stdout);
    } else if (strstr(buffer, "\"method\":\"tools/list\"") != NULL) {
      fputs("{\"jsonrpc\":\"2.0\",\"id\":\"pixel-tools\",\"result\":{\"resultType\":\"complete\",\"tools\":[{\"name\":\"analyze\",\"title\":\"Analyze text\",\"description\":\"Return deterministic local text facts.\",\"inputSchema\":{\"type\":\"object\",\"additionalProperties\":false,\"required\":[\"text\"],\"properties\":{\"text\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":1024}}},\"outputSchema\":{\"type\":\"object\",\"additionalProperties\":false,\"required\":[\"length\"],\"properties\":{\"length\":{\"type\":\"integer\",\"minimum\":0,\"maximum\":1024}}}}]}}\n", stdout);
    } else if (strstr(buffer, "\"method\":\"tools/call\"") != NULL && strstr(buffer, "\"name\":\"analyze\"") != NULL && strstr(buffer, "\"text\":\"pixel-live\"") != NULL) {
      fputs("{\"jsonrpc\":\"2.0\",\"id\":\"pixel-call\",\"result\":{\"resultType\":\"complete\",\"content\":[{\"type\":\"text\",\"text\":\"{\\\"length\\\":10}\"}],\"structuredContent\":{\"length\":10},\"isError\":false}}\n", stdout);
    } else {
      fputs("{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32601,\"message\":\"unsupported\"}}\n", stdout);
    }
    fflush(stdout);
  }
  return ferror(stdin) ? 1 : 0;
}
