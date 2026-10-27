// ============================================================================
// ODS-Setup.exe -- Phase-Wise Native Windows Installer Wizard for ODS
// ============================================================================
// Compiles with built-in Windows .NET Framework csc.exe (zero external SDKs).
// Drives installers/windows-desktop.ps1 across all 6 installation phases,
// parsing "[ODS-PHASE] {json}" progress lines to render a modern dark-mode
// Windows installer experience, and supports --dry-run-check for CI proof.
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace OdsNativeSetup
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            string installDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                "ods"
            );
            string gpuMode = "high_performance";
            bool dryRunCheck = false;

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i] ?? "";
                if (a == "--version" || a == "-v")
                {
                    Console.WriteLine("ODS-Setup.exe 3.0.0 (windows-desktop)");
                    return 0;
                }
                if (a == "--dry-run-check")
                {
                    dryRunCheck = true;
                }
                else if (a.StartsWith("--install-dir=", StringComparison.OrdinalIgnoreCase))
                {
                    installDir = a.Substring("--install-dir=".Length).Trim('"');
                }
                else if (a.StartsWith("--gpu-mode=", StringComparison.OrdinalIgnoreCase))
                {
                    gpuMode = a.Substring("--gpu-mode=".Length).Trim();
                }
            }

            if (dryRunCheck)
            {
                string scriptPath = ResolveInstallerScriptPath();
                string json = string.Format(
                    "{{\"executable\":\"ODS-Setup.exe\",\"version\":\"3.0.0\",\"profile\":\"windows-desktop\",\"defaultInstallDir\":\"{0}\",\"defaultGpuPreference\":\"{1}\",\"installerScriptFound\":{2}}}",
                    EscapeJson(installDir),
                    EscapeJson(gpuMode),
                    (!string.IsNullOrEmpty(scriptPath) && File.Exists(scriptPath)) ? "true" : "false"
                );
                Console.WriteLine(json);
                return 0;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupWizardForm(installDir, gpuMode));
            return 0;
        }

        internal static string ResolveInstallerScriptPath()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string[] candidates = new string[]
            {
                Path.Combine(baseDir, "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "..", "installers", "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "..", "..", "installers", "windows-desktop.ps1")
            };
            foreach (string c in candidates)
            {
                try
                {
                    string full = Path.GetFullPath(c);
                    if (File.Exists(full))
                    {
                        return full;
                    }
                }
                catch
                {
                }
            }
            return "";
        }

        internal static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }

    internal sealed class SetupWizardForm : Form
    {
        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        private readonly TextBox _installDirBox;
        private readonly ComboBox _gpuModeCombo;
        private readonly ProgressBar _progressBar;
        private readonly Label _phaseTitleLabel;
        private readonly Label _phaseDetailLabel;
        private readonly Label[] _phaseChecks;
        private readonly Button _installButton;
        private readonly Button _launchButton;
        private string _installDir;
        private bool _isInstalling;

        public SetupWizardForm(string defaultInstallDir, string defaultGpuMode)
        {
            _installDir = defaultInstallDir;

            Text = "ODS Windows Native Setup";
            Size = new Size(680, 520);
            MinimumSize = new Size(680, 520);
            MaximumSize = new Size(680, 520);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            BackColor = Color.FromArgb(11, 15, 25);
            ForeColor = Color.FromArgb(241, 245, 249);
            Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);

            try
            {
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch
            {
            }

            Label headerLabel = new Label
            {
                Text = "Open Dev Society — Windows Native Desktop",
                Font = new Font("Segoe UI Semibold", 15f, FontStyle.Bold),
                ForeColor = Color.FromArgb(56, 189, 248),
                Location = new Point(28, 22),
                AutoSize = true
            };
            Controls.Add(headerLabel);

            Label subHeaderLabel = new Label
            {
                Text = "Zero Docker • Zero WSL • Direct GPU Acceleration (CUDA / Vulkan) • Full ODS Portal",
                Font = new Font("Segoe UI", 9.25f, FontStyle.Regular),
                ForeColor = Color.FromArgb(148, 163, 184),
                Location = new Point(30, 54),
                AutoSize = true
            };
            Controls.Add(subHeaderLabel);

            Label dirLabel = new Label
            {
                Text = "Installation Directory:",
                Location = new Point(30, 96),
                AutoSize = true,
                ForeColor = Color.FromArgb(203, 213, 225)
            };
            Controls.Add(dirLabel);

            _installDirBox = new TextBox
            {
                Text = _installDir,
                Location = new Point(30, 120),
                Width = 600,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(_installDirBox);

            Label gpuLabel = new Label
            {
                Text = "Windows Graphics Preference (HKCU DirectX UserGpuPreferences):",
                Location = new Point(30, 158),
                AutoSize = true,
                ForeColor = Color.FromArgb(203, 213, 225)
            };
            Controls.Add(gpuLabel);

            _gpuModeCombo = new ComboBox
            {
                Location = new Point(30, 182),
                Width = 600,
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.White
            };
            _gpuModeCombo.Items.Add("High Performance — Prefer Discrete GPU First (GpuPreference=2)");
            _gpuModeCombo.Items.Add("Power Saving — Prefer Integrated GPU / CPU (GpuPreference=1)");
            _gpuModeCombo.Items.Add("Let Windows Decide (GpuPreference=0)");
            _gpuModeCombo.SelectedIndex = defaultGpuMode == "power_saving" ? 1 : (defaultGpuMode == "let_windows_decide" ? 2 : 0);
            Controls.Add(_gpuModeCombo);

            string[] phaseNames = new string[]
            {
                "Phase 1: Hardware & DirectX GPU Preference Discovery",
                "Phase 2: Pinned Embeddable Python 3.12 Runtime",
                "Phase 3: Native llama.cpp GPU/CPU Qualification",
                "Phase 4: Resumable SHA-256 Verified GGUF Model",
                "Phase 5: Owner-Preserving .env & Installed Apps Registration",
                "Phase 6: Native Service Startup & Desktop Shortcuts"
            };
            _phaseChecks = new Label[phaseNames.Length];
            for (int i = 0; i < phaseNames.Length; i++)
            {
                _phaseChecks[i] = new Label
                {
                    Text = "○  " + phaseNames[i],
                    Location = new Point(32, 225 + (i * 25)),
                    AutoSize = true,
                    ForeColor = Color.FromArgb(148, 163, 184)
                };
                Controls.Add(_phaseChecks[i]);
            }

            _phaseTitleLabel = new Label
            {
                Text = "Ready to install ODS Windows Native Desktop.",
                Location = new Point(30, 384),
                AutoSize = true,
                Font = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(226, 232, 240)
            };
            Controls.Add(_phaseTitleLabel);

            _phaseDetailLabel = new Label
            {
                Text = "Click 'Install ODS Desktop' to begin phase-wise setup.",
                Location = new Point(30, 406),
                Width = 600,
                Height = 20,
                ForeColor = Color.FromArgb(148, 163, 184)
            };
            Controls.Add(_phaseDetailLabel);

            _progressBar = new ProgressBar
            {
                Location = new Point(30, 430),
                Width = 420,
                Height = 28,
                Minimum = 0,
                Maximum = 100,
                Value = 0
            };
            Controls.Add(_progressBar);

            _installButton = new Button
            {
                Text = "Install ODS Desktop",
                Location = new Point(465, 428),
                Size = new Size(165, 32),
                BackColor = Color.FromArgb(14, 165, 233),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            _installButton.FlatAppearance.BorderSize = 0;
            _installButton.Click += OnInstallClick;
            Controls.Add(_installButton);

            _launchButton = new Button
            {
                Text = "Launch ODS",
                Location = new Point(465, 428),
                Size = new Size(165, 32),
                BackColor = Color.FromArgb(16, 185, 129),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Visible = false
            };
            _launchButton.FlatAppearance.BorderSize = 0;
            _launchButton.Click += OnLaunchClick;
            Controls.Add(_launchButton);
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

        private void OnInstallClick(object sender, EventArgs e)
        {
            if (_isInstalling) return;
            _isInstalling = true;
            _installButton.Enabled = false;
            _installDirBox.Enabled = false;
            _gpuModeCombo.Enabled = false;

            _installDir = _installDirBox.Text.Trim();
            string mode = _gpuModeCombo.SelectedIndex == 1
                ? "power_saving"
                : (_gpuModeCombo.SelectedIndex == 2 ? "let_windows_decide" : "high_performance");

            Thread worker = new Thread(() => RunInstallerProcess(_installDir, mode))
            {
                IsBackground = true
            };
            worker.Start();
        }

        private void RunInstallerProcess(string installDir, string gpuMode)
        {
            string scriptPath = Program.ResolveInstallerScriptPath();
            if (string.IsNullOrEmpty(scriptPath) || !File.Exists(scriptPath))
            {
                BeginInvoke((Action)(() =>
                {
                    _phaseTitleLabel.Text = "Installer script not found.";
                    _phaseDetailLabel.Text = "Ensure installers\\windows-desktop.ps1 is present.";
                    _installButton.Enabled = true;
                    _isInstalling = false;
                }));
                return;
            }

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = string.Format(
                    "-NoProfile -ExecutionPolicy Bypass -File \"{0}\" -InstallDir \"{1}\" -DefaultGpuPreference {2}",
                    scriptPath,
                    installDir,
                    gpuMode
                ),
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8
            };

            try
            {
                using (Process proc = Process.Start(psi))
                {
                    string line;
                    while ((line = proc.StandardOutput.ReadLine()) != null)
                    {
                        if (line.StartsWith("[ODS-PHASE]", StringComparison.Ordinal))
                        {
                            string json = line.Substring("[ODS-PHASE]".Length).Trim();
                            HandlePhaseJson(json);
                        }
                    }
                    proc.WaitForExit();
                    int code = proc.ExitCode;
                    BeginInvoke((Action)(() =>
                    {
                        if (code == 0)
                        {
                            _progressBar.Value = 100;
                            _phaseTitleLabel.Text = "ODS Windows Native Desktop Installed Successfully!";
                            _phaseDetailLabel.Text = "Click 'Launch ODS' to open your local AI portal.";
                            _installButton.Visible = false;
                            _launchButton.Visible = true;
                        }
                        else
                        {
                            _phaseTitleLabel.Text = "Installation encountered an error (exit " + code + ").";
                            _installButton.Enabled = true;
                            _isInstalling = false;
                        }
                    }));
                }
            }
            catch (Exception ex)
            {
                BeginInvoke((Action)(() =>
                {
                    _phaseTitleLabel.Text = "Installation error: " + ex.Message;
                    _installButton.Enabled = true;
                    _isInstalling = false;
                }));
            }
        }

        private void HandlePhaseJson(string json)
        {
            int phase = ExtractInt(json, "phase", 1);
            int percent = ExtractInt(json, "percent", 0);
            string status = ExtractString(json, "status");
            string title = ExtractString(json, "title");
            string detail = ExtractString(json, "detail");

            BeginInvoke((Action)(() =>
            {
                if (percent >= 0 && percent <= 100)
                {
                    _progressBar.Value = percent;
                }
                if (!string.IsNullOrEmpty(title))
                {
                    _phaseTitleLabel.Text = string.Format("Phase {0}/6: {1}", phase, title);
                }
                if (!string.IsNullOrEmpty(detail))
                {
                    _phaseDetailLabel.Text = detail;
                }
                int idx = phase - 1;
                if (idx >= 0 && idx < _phaseChecks.Length)
                {
                    string baseText = _phaseChecks[idx].Text.Substring(3);
                    if (status == "completed")
                    {
                        _phaseChecks[idx].Text = "✔  " + baseText;
                        _phaseChecks[idx].ForeColor = Color.FromArgb(52, 211, 153);
                    }
                    else
                    {
                        _phaseChecks[idx].Text = "▶  " + baseText;
                        _phaseChecks[idx].ForeColor = Color.FromArgb(56, 189, 248);
                    }
                }
            }));
        }

        private static int ExtractInt(string json, string key, int fallback)
        {
            Match m = Regex.Match(json, "\"" + key + "\"\\s*:\\s*([0-9]+)");
            int val;
            if (m.Success && int.TryParse(m.Groups[1].Value, out val))
            {
                return val;
            }
            return fallback;
        }

        private static string ExtractString(string json, string key)
        {
            Match m = Regex.Match(json, "\"" + key + "\"\\s*:\\s*\"((?:\\\\.|[^\"])*)\"");
            if (m.Success)
            {
                return m.Groups[1].Value.Replace("\\\"", "\"").Replace("\\\\", "\\");
            }
            return "";
        }

        private void OnLaunchClick(object sender, EventArgs e)
        {
            string odsExe = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "ODS", "app", "current", "ODS.exe"
            );
            if (!File.Exists(odsExe))
            {
                odsExe = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ODS.exe");
            }
            if (File.Exists(odsExe))
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = odsExe,
                    UseShellExecute = true
                });
            }
            Close();
        }
    }
}
