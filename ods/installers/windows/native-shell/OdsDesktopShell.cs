// ============================================================================
// ODS.exe -- Native Windows Desktop Application (Embedded WebView2 + Tray)
// ============================================================================
// Compiles with built-in Windows .NET Framework csc.exe + WebView2 assemblies.
// Features:
//   1. True standalone Desktop Application window (1360x860 resizable) with
//      Microsoft.Web.WebView2.WinForms.WebView2 embedded directly inside
//      ODS.exe (no external browser windows).
//   2. Built-in dark boot splash inside WebView2 while background native
//      llama-server.exe (127.0.0.1:8085) and dashboard-api (127.0.0.1:3101)
//      initialize, automatically navigating to http://localhost:3101/pixel
//      once 127.0.0.1:3101 responds Ready (zero ERR_CONNECTION_REFUSED).
//   3. Native top bar + System Tray (NotifyIcon) with live DirectX
//      UserGpuPreferences toggle (GPU-First vs Power Saving), Windows
//      Graphics Settings launcher, and Figma-style in-app update check.
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

namespace OdsNativeDesktop
{
    internal static class Program
    {
        internal static string DirectXRegSubKey = @"Software\Microsoft\DirectX\UserGpuPreferences";

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [STAThread]
        private static int Main(string[] args)
        {
            string installDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                "ods"
            );
            string appDataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "ODS"
            );

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i] ?? "";
                if (a == "--version" || a == "-v")
                {
                    Console.WriteLine("ODS.exe 3.0.0 (windows-desktop)");
                    return 0;
                }
                if (a.StartsWith("--install-dir=", StringComparison.OrdinalIgnoreCase))
                {
                    installDir = a.Substring("--install-dir=".Length).Trim('"');
                }
                else if (a.StartsWith("--appdata-dir=", StringComparison.OrdinalIgnoreCase))
                {
                    appDataDir = a.Substring("--appdata-dir=".Length).Trim('"');
                }
                else if (a.StartsWith("--reg-subkey=", StringComparison.OrdinalIgnoreCase))
                {
                    DirectXRegSubKey = a.Substring("--reg-subkey=".Length).Trim('"');
                }
            }

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i] ?? "";
                if (a.StartsWith("--set-gpu-preference=", StringComparison.OrdinalIgnoreCase))
                {
                    string mode = a.Substring("--set-gpu-preference=".Length).Trim().ToLowerInvariant();
                    int code = mode == "power_saving" ? 1 : (mode == "let_windows_decide" ? 0 : 2);
                    string llamaExe = Path.Combine(appDataDir, @"runtimes\llama\llama-server.exe");
                    string odsExe = Application.ExecutablePath;
                    SetUserGpuPreference(llamaExe, code);
                    SetUserGpuPreference(odsExe, code);
                    Console.WriteLine(
                        string.Format(
                            "{{\"ok\":true,\"mode\":\"{0}\",\"preferenceCode\":{1}}}",
                            mode,
                            code
                        )
                    );
                    return 0;
                }
                if (a == "--status-json")
                {
                    string llamaExe = Path.Combine(appDataDir, @"runtimes\llama\llama-server.exe");
                    int prefCode = GetUserGpuPreferenceCode(llamaExe, Application.ExecutablePath);
                    string prefMode = prefCode == 1 ? "power_saving" : (prefCode == 0 ? "let_windows_decide" : "high_performance");
                    bool apiUp = IsTcpPortOpen("127.0.0.1", 3101, 250);
                    bool llamaUp = IsTcpPortOpen("127.0.0.1", 8085, 250);
                    string json = string.Format(
                        "{{\"executable\":\"ODS.exe\",\"version\":\"3.0.0\",\"runtimeProfile\":\"windows-desktop\",\"gpuPreferenceMode\":\"{0}\",\"gpuPreferenceCode\":{1},\"preferDiscreteGpu\":{2},\"dashboardApiListening\":{3},\"llamaServerListening\":{4}}}",
                        prefMode,
                        prefCode,
                        (prefCode != 1) ? "true" : "false",
                        apiUp ? "true" : "false",
                        llamaUp ? "true" : "false"
                    );
                    Console.WriteLine(json);
                    return 0;
                }
            }

            bool createdNew;
            using (Mutex mutex = new Mutex(true, @"Local\ODSWindowsNativeDesktopShell", out createdNew))
            {
                if (!createdNew)
                {
                    // Focus existing ODS.exe window if already running
                    try
                    {
                        Process current = Process.GetCurrentProcess();
                        foreach (Process p in Process.GetProcessesByName(current.ProcessName))
                        {
                            if (p.Id != current.Id && p.MainWindowHandle != IntPtr.Zero)
                            {
                                ShowWindow(p.MainWindowHandle, 9); // SW_RESTORE
                                SetForegroundWindow(p.MainWindowHandle);
                                break;
                            }
                        }
                    }
                    catch
                    {
                    }
                    return 0;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new DesktopShellForm(installDir, appDataDir));
            }
            return 0;
        }

        internal static int GetUserGpuPreferenceCode(params string[] exePaths)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(DirectXRegSubKey, false))
                {
                    if (key == null) return 2; // GPU-First default
                    foreach (string p in exePaths)
                    {
                        if (string.IsNullOrEmpty(p)) continue;
                        string raw = key.GetValue(p) as string;
                        int parsed = ParseGpuPreferenceCode(raw);
                        if (parsed >= 0) return parsed;
                    }
                }
            }
            catch
            {
            }
            return 2; // Default High Performance (discrete GPU)
        }

        internal static int ParseGpuPreferenceCode(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return -1;
            string[] parts = raw.Split(';');
            foreach (string part in parts)
            {
                string trimmed = part.Trim();
                if (trimmed.StartsWith("GpuPreference=", StringComparison.OrdinalIgnoreCase))
                {
                    string val = trimmed.Substring("GpuPreference=".Length).Trim();
                    int code;
                    if (int.TryParse(val, out code) && (code == 0 || code == 1 || code == 2))
                    {
                        return code;
                    }
                }
            }
            return -1;
        }

        internal static void SetUserGpuPreference(string exePath, int preferenceCode)
        {
            if (string.IsNullOrEmpty(exePath)) return;
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(DirectXRegSubKey))
                {
                    if (key == null) return;
                    string existing = (key.GetValue(exePath) as string) ?? "";
                    List<string> preserved = new List<string>();
                    foreach (string segment in existing.Split(';'))
                    {
                        string s = segment.Trim();
                        if (string.IsNullOrEmpty(s)) continue;
                        if (!s.StartsWith("GpuPreference=", StringComparison.OrdinalIgnoreCase))
                        {
                            preserved.Add(s);
                        }
                    }
                    preserved.Add("GpuPreference=" + preferenceCode);
                    string updated = string.Join(";", preserved.ToArray()) + ";";
                    key.SetValue(exePath, updated, RegistryValueKind.String);
                }
            }
            catch
            {
            }
        }

        internal static bool IsTcpPortOpen(string host, int port, int timeoutMs)
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult res = client.BeginConnect(host, port, null, null);
                    bool signaled = res.AsyncWaitHandle.WaitOne(timeoutMs);
                    if (!signaled) return false;
                    client.EndConnect(res);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }
    }

    internal sealed class DesktopShellForm : Form
    {
        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        private const string PortalTargetUrl = "http://localhost:3101/pixel";

        private readonly string _installDir;
        private readonly string _appDataDir;
        private readonly NotifyIcon _trayIcon;
        private readonly ToolStripMenuItem _gpuHighPerfMenu;
        private readonly ToolStripMenuItem _gpuPowerSaveMenu;
        private readonly Panel _topBarPanel;
        private readonly Label _serviceBadgeLabel;
        private readonly ComboBox _gpuSelectorCombo;
        private readonly Button _updateBtn;
        private readonly WebView2 _webView;
        private readonly System.Windows.Forms.Timer _statusTimer;
        private Process _managedApiProcess;
        private Process _managedLlamaProcess;
        private bool _webViewReady;
        private bool _portalLoaded;
        private bool _suppressComboEvent;
        private DateTime _lastStartAttemptUtc = DateTime.MinValue;

        public DesktopShellForm(string installDir, string appDataDir)
        {
            _installDir = installDir;
            _appDataDir = appDataDir;

            Text = "ODS — Open Dev Society";
            Size = new Size(1360, 860);
            MinimumSize = new Size(960, 640);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.Sizable;
            BackColor = Color.FromArgb(11, 15, 25);
            ForeColor = Color.FromArgb(241, 245, 249);
            Font = new Font("Segoe UI", 9f, FontStyle.Regular);

            Icon appIcon = SystemIcons.Application;
            try
            {
                appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) ?? SystemIcons.Application;
                Icon = appIcon;
            }
            catch
            {
            }

            // 1. System Tray Icon & Context Menu
            ContextMenuStrip trayMenu = new ContextMenuStrip();
            trayMenu.Items.Add("Show ODS Window", null, (s, e) => RestoreMainWindow());
            trayMenu.Items.Add("Reload Portal UI", null, (s, e) => ReloadPortal());
            trayMenu.Items.Add(new ToolStripSeparator());

            _gpuHighPerfMenu = new ToolStripMenuItem("GPU Mode: High Performance (Discrete GPU)", null, (s, e) => ApplyGpuMode(2));
            _gpuPowerSaveMenu = new ToolStripMenuItem("GPU Mode: Power Saving (iGPU / CPU)", null, (s, e) => ApplyGpuMode(1));
            trayMenu.Items.Add(_gpuHighPerfMenu);
            trayMenu.Items.Add(_gpuPowerSaveMenu);
            trayMenu.Items.Add("Open Windows Graphics Settings...", null, (s, e) => OpenWindowsGraphicsSettings());
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add("Check for ODS Updates...", null, (s, e) => TriggerUpdateCheck());
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add("Quit ODS", null, (s, e) => ExitApplication());

            _trayIcon = new NotifyIcon
            {
                Icon = appIcon,
                Text = "ODS Windows Native Desktop",
                ContextMenuStrip = trayMenu,
                Visible = true
            };
            _trayIcon.DoubleClick += (s, e) => RestoreMainWindow();

            // 2. Compact Native Top Desktop Bar (40px height)
            _topBarPanel = new Panel
            {
                Dock = DockStyle.Top,
                Height = 40,
                BackColor = Color.FromArgb(15, 23, 42)
            };

            Label brandLabel = new Label
            {
                Text = "ODS Desktop",
                Font = new Font("Segoe UI Semibold", 10f, FontStyle.Bold),
                ForeColor = Color.FromArgb(56, 189, 248),
                Location = new Point(14, 10),
                AutoSize = true
            };
            _topBarPanel.Controls.Add(brandLabel);

            _serviceBadgeLabel = new Label
            {
                Text = "● Starting Native Services...",
                Font = new Font("Segoe UI", 8.75f, FontStyle.Regular),
                ForeColor = Color.FromArgb(251, 191, 36),
                Location = new Point(116, 11),
                AutoSize = true
            };
            _topBarPanel.Controls.Add(_serviceBadgeLabel);

            FlowLayoutPanel rightControls = new FlowLayoutPanel
            {
                Dock = DockStyle.Right,
                AutoSize = true,
                FlowDirection = FlowDirection.LeftToRight,
                WrapContents = false,
                Padding = new Padding(0, 6, 10, 0),
                BackColor = Color.Transparent
            };

            _gpuSelectorCombo = new ComboBox
            {
                Width = 230,
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.FromArgb(226, 232, 240),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Regular)
            };
            _gpuSelectorCombo.Items.Add("GPU: High Performance (Discrete)");
            _gpuSelectorCombo.Items.Add("GPU: Power Saving (iGPU / CPU)");
            _gpuSelectorCombo.SelectedIndexChanged += OnGpuComboChanged;
            rightControls.Controls.Add(_gpuSelectorCombo);

            Button winGpuBtn = new Button
            {
                Text = "OS GPU Settings",
                Size = new Size(112, 26),
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.FromArgb(203, 213, 225),
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Regular)
            };
            winGpuBtn.FlatAppearance.BorderSize = 0;
            winGpuBtn.Click += (s, e) => OpenWindowsGraphicsSettings();
            rightControls.Controls.Add(winGpuBtn);

            _updateBtn = new Button
            {
                Text = "v3.0.0 • Check Updates",
                Size = new Size(142, 26),
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.FromArgb(148, 163, 184),
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Regular)
            };
            _updateBtn.FlatAppearance.BorderSize = 0;
            _updateBtn.Click += (s, e) => TriggerUpdateCheck();
            rightControls.Controls.Add(_updateBtn);

            Button reloadBtn = new Button
            {
                Text = "↻ Reload",
                Size = new Size(76, 26),
                BackColor = Color.FromArgb(14, 165, 233),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.25f, FontStyle.Bold)
            };
            reloadBtn.FlatAppearance.BorderSize = 0;
            reloadBtn.Click += (s, e) => ReloadPortal();
            rightControls.Controls.Add(reloadBtn);

            _topBarPanel.Controls.Add(rightControls);

            // 3. Embedded Chromium WebView2 Canvas (fills the rest of the window)
            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                DefaultBackgroundColor = Color.FromArgb(11, 15, 25)
            };

            // Add _webView first, then _topBarPanel so DockStyle.Fill respects DockStyle.Top
            Controls.Add(_webView);
            Controls.Add(_topBarPanel);

            Load += OnFormLoad;

            ThreadPool.QueueUserWorkItem(_ => EnsureNativeServicesStarted());

            _statusTimer = new System.Windows.Forms.Timer { Interval = 1500 };
            _statusTimer.Tick += (s, e) => RefreshStatusAndNavigateIfReady();
            _statusTimer.Start();

            RefreshStatusAndNavigateIfReady();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int useDark = 1;
                DwmSetWindowAttribute(Handle, 20, ref useDark, sizeof(int));
            }
            catch
            {
            }
        }

        private async void OnFormLoad(object sender, EventArgs e)
        {
            try
            {
                string userDataFolder = Path.Combine(_appDataDir, "webview2-profile");
                Directory.CreateDirectory(userDataFolder);
                CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(null, userDataFolder, null);
                await _webView.EnsureCoreWebView2Async(env);
                _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                _webViewReady = true;

                if (Program.IsTcpPortOpen("127.0.0.1", 3101, 200))
                {
                    _portalLoaded = true;
                    _webView.CoreWebView2.Navigate(PortalTargetUrl);
                }
                else
                {
                    _webView.CoreWebView2.NavigateToString(BuildBootSplashHtml());
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "WebView2 initialization error: " + ex.Message,
                    "ODS Desktop",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
            }
        }

        private static string BuildBootSplashHtml()
        {
            return @"<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<style>
  body {
    margin: 0; height: 100vh; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    background: #0b0f19; color: #f1f5f9;
    font-family: 'Segoe UI', system-ui, sans-serif;
  }
  .card {
    background: #111827; border: 1px solid #1e293b; border-radius: 16px;
    padding: 40px 52px; text-align: center; box-shadow: 0 20px 50px rgba(0,0,0,0.5);
  }
  .spinner {
    width: 44px; height: 44px; margin: 0 auto 20px;
    border: 3px solid #1e293b; border-top-color: #38bdf8;
    border-radius: 50%; animation: spin 0.9s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  h2 { margin: 0 0 8px; font-size: 20px; color: #38bdf8; }
  p { margin: 0; font-size: 13.5px; color: #94a3b8; }
</style>
</head>
<body>
  <div class='card'>
    <div class='spinner'></div>
    <h2>Starting ODS Native Desktop...</h2>
    <p>Initializing local Portal API (127.0.0.1:3101) and GPU inference engine (127.0.0.1:8085)</p>
  </div>
</body>
</html>";
        }

        private void RestoreMainWindow()
        {
            Show();
            if (WindowState == FormWindowState.Minimized)
            {
                WindowState = FormWindowState.Normal;
            }
            Activate();
        }

        private void ReloadPortal()
        {
            if (!_webViewReady || _webView.CoreWebView2 == null) return;
            if (Program.IsTcpPortOpen("127.0.0.1", 3101, 200))
            {
                _portalLoaded = true;
                _webView.CoreWebView2.Navigate(PortalTargetUrl);
            }
            else
            {
                EnsureNativeServicesStarted();
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                return;
            }
            base.OnFormClosing(e);
        }

        private void OnGpuComboChanged(object sender, EventArgs e)
        {
            if (_suppressComboEvent) return;
            int code = _gpuSelectorCombo.SelectedIndex == 1 ? 1 : 2;
            ApplyGpuMode(code);
        }

        private Dictionary<string, string> ReadDotEnv()
        {
            Dictionary<string, string> env = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            string envFile = Path.Combine(_installDir, ".env");
            if (!File.Exists(envFile)) return env;
            try
            {
                foreach (string line in File.ReadAllLines(envFile, Encoding.UTF8))
                {
                    string s = (line ?? "").Trim();
                    if (string.IsNullOrEmpty(s) || s.StartsWith("#")) continue;
                    int eq = s.IndexOf('=');
                    if (eq <= 0) continue;
                    string k = s.Substring(0, eq).Trim();
                    string v = s.Substring(eq + 1).Trim().Trim('\'', '"');
                    env[k] = v;
                }
            }
            catch
            {
            }
            return env;
        }

        private void EnsureNativeServicesStarted()
        {
            Dictionary<string, string> env = ReadDotEnv();
            string llamaExe = Path.Combine(_appDataDir, @"runtimes\llama\llama-server.exe");
            int gpuPref = Program.GetUserGpuPreferenceCode(llamaExe, Application.ExecutablePath);

            // 1. Start llama-server.exe if present and port 8085 is not yet open
            if (!Program.IsTcpPortOpen("127.0.0.1", 8085, 200) && File.Exists(llamaExe))
            {
                string ggufName = env.ContainsKey("GGUF_FILE") ? env["GGUF_FILE"] : "";
                string modelPath = !string.IsNullOrEmpty(ggufName)
                    ? Path.Combine(_installDir, "models", ggufName)
                    : "";
                if (string.IsNullOrEmpty(modelPath) || !File.Exists(modelPath))
                {
                    string modelsDir = Path.Combine(_installDir, "models");
                    if (Directory.Exists(modelsDir))
                    {
                        string[] ggufs = Directory.GetFiles(modelsDir, "*.gguf");
                        if (ggufs.Length > 0) modelPath = ggufs[0];
                    }
                }
                if (!string.IsNullOrEmpty(modelPath) && File.Exists(modelPath))
                {
                    string ngl = (gpuPref == 1) ? "0 --device none" : "99";
                    try
                    {
                        ProcessStartInfo lpsi = new ProcessStartInfo
                        {
                            FileName = llamaExe,
                            Arguments = string.Format("-m \"{0}\" --host 127.0.0.1 --port 8085 -c 4096 -ngl {1}", modelPath, ngl),
                            WorkingDirectory = Path.GetDirectoryName(llamaExe),
                            UseShellExecute = false,
                            CreateNoWindow = true
                        };
                        _managedLlamaProcess = Process.Start(lpsi);
                    }
                    catch
                    {
                    }
                }
            }

            // 2. Start dashboard-api on 127.0.0.1:3101 if not yet open
            if (!Program.IsTcpPortOpen("127.0.0.1", 3101, 200))
            {
                string pyExe = env.ContainsKey("ODS_PYTHON_EXE") ? env["ODS_PYTHON_EXE"] : "";
                if (string.IsNullOrEmpty(pyExe) || !File.Exists(pyExe))
                {
                    string candidate = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        @"Python\pythoncore-3.14-64\python.exe"
                    );
                    if (File.Exists(candidate)) pyExe = candidate;
                }

                string apiDir = "";
                if (env.ContainsKey("ODS_DASHBOARD_API_DIR") && Directory.Exists(env["ODS_DASHBOARD_API_DIR"]))
                {
                    apiDir = env["ODS_DASHBOARD_API_DIR"];
                }
                else if (Directory.Exists(Path.Combine(_installDir, "dashboard-api")))
                {
                    apiDir = Path.Combine(_installDir, "dashboard-api");
                }
                else if (Directory.Exists(Path.Combine(_appDataDir, @"app\current\dashboard-api")))
                {
                    apiDir = Path.Combine(_appDataDir, @"app\current\dashboard-api");
                }
                else
                {
                    string repoRoot = env.ContainsKey("ODS_REPO_ROOT") ? env["ODS_REPO_ROOT"] : "";
                    string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                    string[] candidates = new string[]
                    {
                        repoRoot,
                        Path.Combine(baseDir, "..", ".."),
                        Path.Combine(baseDir, ".."),
                        @"D:\ODS\ods"
                    };
                    foreach (string c in candidates)
                    {
                        if (string.IsNullOrEmpty(c)) continue;
                        try
                        {
                            string full = Path.GetFullPath(c);
                            string checkDir = Path.Combine(full, @"extensions\services\dashboard-api");
                            if (Directory.Exists(checkDir))
                            {
                                apiDir = checkDir;
                                break;
                            }
                        }
                        catch
                        {
                        }
                    }
                }

                if (!string.IsNullOrEmpty(pyExe) && File.Exists(pyExe) && !string.IsNullOrEmpty(apiDir) && Directory.Exists(apiDir))
                {
                    try
                    {
                        string staticDir = env.ContainsKey("ODS_DASHBOARD_DIST")
                            ? env["ODS_DASHBOARD_DIST"]
                            : (env.ContainsKey("STATIC_DIR") ? env["STATIC_DIR"] : Path.Combine(_installDir, "dashboard-dist"));
                        if (!Directory.Exists(staticDir))
                        {
                            string[] staticCandidates = new string[]
                            {
                                Path.Combine(_installDir, "dashboard-dist"),
                                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "dashboard-dist"),
                                Path.Combine(apiDir, @"..\dashboard\dist")
                            };
                            foreach (string sc in staticCandidates)
                            {
                                if (Directory.Exists(sc)) { staticDir = Path.GetFullPath(sc); break; }
                            }
                        }
                        string extDir = Path.GetFullPath(Path.Combine(apiDir, ".."));

                        ProcessStartInfo apsi = new ProcessStartInfo
                        {
                            FileName = pyExe,
                            Arguments = "-m uvicorn main:app --host 127.0.0.1 --port 3101",
                            WorkingDirectory = apiDir,
                            UseShellExecute = false,
                            CreateNoWindow = true
                        };
                        apsi.EnvironmentVariables["ODS_RUNTIME_PROFILE"] = "windows-desktop";
                        apsi.EnvironmentVariables["ODS_MODE"] = "local";
                        apsi.EnvironmentVariables["LLM_BACKEND"] = "llama-server";
                        apsi.EnvironmentVariables["AGENT_URL"] = "";
                        apsi.EnvironmentVariables["ODS_DATA_DIR"] = _installDir;
                        apsi.EnvironmentVariables["DATA_DIR"] = _installDir;
                        apsi.EnvironmentVariables["ODS_INSTALL_DIR"] = _installDir;
                        apsi.EnvironmentVariables["INSTALL_DIR"] = _installDir;
                        apsi.EnvironmentVariables["ODS_DASHBOARD_DIST"] = staticDir;
                        apsi.EnvironmentVariables["STATIC_DIR"] = staticDir;
                        apsi.EnvironmentVariables["ODS_EXTENSIONS_DIR"] = extDir;
                        apsi.EnvironmentVariables["LLAMA_PORT"] = "8085";
                        apsi.EnvironmentVariables["ODS_NATIVE_LLM_URL"] = "http://127.0.0.1:8085";
                        apsi.EnvironmentVariables["ODS_LLAMA_ENDPOINT"] = "http://127.0.0.1:8085";
                        apsi.EnvironmentVariables["DASHBOARD_API_KEY"] = env.ContainsKey("DASHBOARD_API_KEY") && !string.IsNullOrEmpty(env["DASHBOARD_API_KEY"])
                            ? env["DASHBOARD_API_KEY"]
                            : "ods-windows-desktop-local-key";
                        if (env.ContainsKey("DASHBOARD_PASSWORD"))
                        {
                            apsi.EnvironmentVariables["DASHBOARD_PASSWORD"] = env["DASHBOARD_PASSWORD"];
                        }
                        _managedApiProcess = Process.Start(apsi);
                    }
                    catch
                    {
                    }
                }
            }
        }

        private void ApplyGpuMode(int code)
        {
            string llamaExe = Path.Combine(_appDataDir, @"runtimes\llama\llama-server.exe");
            Program.SetUserGpuPreference(llamaExe, code);
            Program.SetUserGpuPreference(Application.ExecutablePath, code);
            RefreshStatusAndNavigateIfReady();
        }

        private void RefreshStatusAndNavigateIfReady()
        {
            string llamaExe = Path.Combine(_appDataDir, @"runtimes\llama\llama-server.exe");
            int code = Program.GetUserGpuPreferenceCode(llamaExe, Application.ExecutablePath);
            _gpuHighPerfMenu.Checked = (code == 2);
            _gpuPowerSaveMenu.Checked = (code == 1);

            _suppressComboEvent = true;
            _gpuSelectorCombo.SelectedIndex = (code == 1) ? 1 : 0;
            _suppressComboEvent = false;

            bool apiUp = Program.IsTcpPortOpen("127.0.0.1", 3101, 150);
            bool llamaUp = Program.IsTcpPortOpen("127.0.0.1", 8085, 150);

            if ((!apiUp || !llamaUp) && (DateTime.UtcNow - _lastStartAttemptUtc).TotalSeconds >= 6.0)
            {
                _lastStartAttemptUtc = DateTime.UtcNow;
                ThreadPool.QueueUserWorkItem(_ => EnsureNativeServicesStarted());
            }

            if (apiUp && llamaUp)
            {
                _serviceBadgeLabel.Text = "● Portal API Online • llama.cpp GPU Engine Online";
                _serviceBadgeLabel.ForeColor = Color.FromArgb(52, 211, 153);
            }
            else if (apiUp)
            {
                _serviceBadgeLabel.Text = "● Portal API Online • llama.cpp Standby";
                _serviceBadgeLabel.ForeColor = Color.FromArgb(56, 189, 248);
            }
            else
            {
                _serviceBadgeLabel.Text = "● Starting Native Services (127.0.0.1:3101)...";
                _serviceBadgeLabel.ForeColor = Color.FromArgb(251, 191, 36);
            }

            if (apiUp && _webViewReady && !_portalLoaded && _webView.CoreWebView2 != null)
            {
                _portalLoaded = true;
                _webView.CoreWebView2.Navigate(PortalTargetUrl);
            }
        }

        private void OpenWindowsGraphicsSettings()
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "ms-settings:display-advancedgraphics",
                    UseShellExecute = true
                });
            }
            catch
            {
            }
        }

        private void TriggerUpdateCheck()
        {
            _updateBtn.Text = "Checking GitHub...";
            ThreadPool.QueueUserWorkItem(_ =>
            {
                string btnText = "v3.0.0 (Up to date)";
                try
                {
                    HttpWebRequest req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:3101/api/version?force=true");
                    req.Timeout = 3500;
                    using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                    using (StreamReader reader = new StreamReader(resp.GetResponseStream()))
                    {
                        string body = reader.ReadToEnd();
                        if (body.Contains("\"update_available\":true"))
                        {
                            btnText = "Update Available!";
                        }
                    }
                }
                catch
                {
                    btnText = "v3.0.0 Ready";
                }
                BeginInvoke((Action)(() => { _updateBtn.Text = btnText; }));
            });
        }

        private void ExitApplication()
        {
            _statusTimer.Stop();
            _trayIcon.Visible = false;
            _trayIcon.Dispose();
            try
            {
                if (_managedApiProcess != null && !_managedApiProcess.HasExited)
                {
                    _managedApiProcess.Kill();
                }
            }
            catch
            {
            }
            try
            {
                if (_managedLlamaProcess != null && !_managedLlamaProcess.HasExited)
                {
                    _managedLlamaProcess.Kill();
                }
            }
            catch
            {
            }
            Application.Exit();
        }
    }
}
