// SPDX-License-Identifier: Apache-2.0
// Test-only publisher boundary; never installs or changes real WebView2.
using System;
using System.IO;
using Microsoft.Win32;

static class WebViewBootstrapperFixture
{
    static int Main(string[] args)
    {
        string root = Environment.GetEnvironmentVariable("ODS_WEBVIEW_TEST_ROOT");
        string id = Environment.GetEnvironmentVariable("ODS_WEBVIEW_TEST_ID");
        Guid parsed;
        if (!Guid.TryParseExact(id, "D", out parsed) || String.IsNullOrEmpty(root)) return 90;
        root = Path.GetFullPath(root);
        if (Path.GetFileName(root) != "ods-prerequisite-" + id || !Directory.Exists(root)) return 91;
        File.WriteAllText(Path.Combine(root, "bootstrapper-argv.txt"), String.Join("|", args));
        if (args.Length != 2 || args[0] != "/silent" || args[1] != "/install") return 90;
        string state = File.ReadAllText(Path.Combine(root, "bootstrapper-state.txt")).Trim();
        File.AppendAllText(Path.Combine(root, "bootstrapper-attempts.txt"), state + Environment.NewLine);
        File.WriteAllText(Path.Combine(root, "bootstrapper-exit.txt"), state == "nonzero-installed" || state == "fail" ? "13" : "0");
        if (state == "fail") return 13;
        if (state == "not-installed") return 0;
        if (state != "success" && state != "old" && state != "nonzero-installed") return 92;
        using (var key = Registry.CurrentUser.CreateSubKey("Software\\ODS\\Tests\\WebView2\\" + id))
            key.SetValue("pv", state == "old" ? "154.0.4258.30" : "154.0.4258.31", RegistryValueKind.String);
        return state == "nonzero-installed" ? 13 : 0;
    }
}
