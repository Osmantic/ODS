// ============================================================================
// ODS-Setup.exe -- Phase-Wise Native Windows Installer Wizard for ODS
// ============================================================================
// Compiles with built-in Windows .NET Framework csc.exe (zero external SDKs).
// Drives installers/windows-desktop.ps1 across all 6 installation phases:
//   Phase 1: Hardware & Tier Classification (GPU, RAM, Disk, Profile selection)
//   Phase 2: Pinned Embeddable Python 3.12 Runtime
//   Phase 3: Native llama.cpp GPU/CPU Qualification
//   Phase 4: Resumable SHA-256 Verified GGUF Model Download from Hugging Face
//   Phase 5: Owner-Preserving .env & Installed Apps Registration
//   Phase 6: Native Service Startup & Desktop Shortcuts
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
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
            string installProfile = "recommended";
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
                else if (a.StartsWith("--profile=", StringComparison.OrdinalIgnoreCase))
                {
                    installProfile = a.Substring("--profile=".Length).Trim();
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
            Application.Run(new SetupWizardForm(installDir, gpuMode, installProfile));
            return 0;
        }

        internal static string ResolveInstallerScriptPath()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string[] candidates = new string[]
            {
                Path.Combine(baseDir, "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "..", "installers", "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "installers", "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "windows-desktop.ps1"),
                Path.Combine(baseDir, "..", "..", "..", "installers", "windows-desktop.ps1"),
                Path.Combine(localApp, @"ODS\app\current\windows-desktop.ps1"),
                @"D:\ODS\ods\dist\windows-desktop\windows-desktop.ps1",
                @"D:\ODS\ods\installers\windows-desktop.ps1",
                @"D:\ODS\dist\windows-desktop\windows-desktop.ps1",
                @"D:\ODS\installers\windows-desktop.ps1"
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

        private static readonly string[] PhaseNames = new string[]
        {
            "Phase 1: Hardware & Tier Classification",
            "Phase 2: Pinned Embeddable Python 3.12 Runtime",
            "Phase 3: Native llama.cpp GPU/CPU Qualification",
            "Phase 4: Resumable Verified AI Model Download",
            "Phase 5: Owner-Preserving .env & Installed Apps Registration",
            "Phase 6: Native Service Startup & Desktop Shortcuts"
        };

        private readonly Label _hwSummaryLabel;
        private readonly Label _hwTierLabel;
        private readonly ComboBox _profileCombo;
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

        public SetupWizardForm(string defaultInstallDir, string defaultGpuMode, string defaultProfile)
        {
            _installDir = defaultInstallDir;

            Text = "ODS Windows Native Setup";
            Size = new Size(720, 620);
            MinimumSize = new Size(720, 620);
            MaximumSize = new Size(720, 620);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            BackColor = Color.FromArgb(11, 15, 25);
            ForeColor = Color.FromArgb(241, 245, 249);
            Font = new Font("Segoe UI", 9.25f, FontStyle.Regular);

            try
            {
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch
            {
            }

            Label headerLabel = new Label
            {
                Text = "Osmantic Deployment System — Windows Native Desktop",
                Font = new Font("Segoe UI Semibold", 15f, FontStyle.Bold),
                ForeColor = Color.FromArgb(56, 189, 248),
                Location = new Point(28, 16),
                AutoSize = true
            };
            Controls.Add(headerLabel);

            Label subHeaderLabel = new Label
            {
                Text = "Zero Docker • Zero WSL • Direct GPU Acceleration (CUDA / Vulkan) • Live Hugging Face Downloads",
                Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                ForeColor = Color.FromArgb(148, 163, 184),
                Location = new Point(30, 46),
                AutoSize = true
            };
            Controls.Add(subHeaderLabel);

            // Hardware Telemetry & Tier Card
            Panel hwCard = new Panel
            {
                Location = new Point(28, 74),
                Size = new Size(650, 64),
                BackColor = Color.FromArgb(19, 26, 42),
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(hwCard);

            _hwSummaryLabel = new Label
            {
                Text = "Detecting system hardware telemetry...",
                Location = new Point(14, 10),
                AutoSize = true,
                Font = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(241, 245, 249)
            };
            hwCard.Controls.Add(_hwSummaryLabel);

            _hwTierLabel = new Label
            {
                Text = "Analyzing hardware classification...",
                Location = new Point(14, 34),
                AutoSize = true,
                Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                ForeColor = Color.FromArgb(52, 211, 153)
            };
            hwCard.Controls.Add(_hwTierLabel);

            // Installation Profile / Tier selection
            Label profileLabel = new Label
            {
                Text = "Installation Profile & Local AI Model:",
                Location = new Point(28, 148),
                AutoSize = true,
                ForeColor = Color.FromArgb(203, 213, 225)
            };
            Controls.Add(profileLabel);

            _profileCombo = new ComboBox
            {
                Location = new Point(28, 170),
                Width = 650,
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.White
            };
            _profileCombo.Items.Add("Recommended (Core Stack) — Qwen 3.5 2B (Optimized for your hardware, ~1.3 GB download)");
            _profileCombo.Items.Add("Full Stack (High Capability) — Qwen 3.5 9B (Higher Accuracy & Reasoning, ~5.5 GB download)");
            _profileCombo.Items.Add("Minimal (Power Saving) — Qwen 3.5 2B (Ultra-lightweight CPU fallback, ~1.3 GB download)");
            _profileCombo.SelectedIndex = (defaultProfile == "full") ? 1 : ((defaultProfile == "minimal") ? 2 : 0);
            Controls.Add(_profileCombo);

            // Installation Directory
            Label dirLabel = new Label
            {
                Text = "Installation Directory:",
                Location = new Point(28, 206),
                AutoSize = true,
                ForeColor = Color.FromArgb(203, 213, 225)
            };
            Controls.Add(dirLabel);

            _installDirBox = new TextBox
            {
                Text = _installDir,
                Location = new Point(28, 228),
                Width = 650,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
            Controls.Add(_installDirBox);

            // Windows Graphics Preference
            Label gpuLabel = new Label
            {
                Text = "Windows Graphics Preference (DirectX UserGpuPreferences):",
                Location = new Point(28, 262),
                AutoSize = true,
                ForeColor = Color.FromArgb(203, 213, 225)
            };
            Controls.Add(gpuLabel);

            _gpuModeCombo = new ComboBox
            {
                Location = new Point(28, 284),
                Width = 650,
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.White
            };
            _gpuModeCombo.Items.Add("High Performance — Prefer Dedicated GPU First (GpuPreference=2)");
            _gpuModeCombo.Items.Add("Power Saving — Prefer Integrated GPU / CPU (GpuPreference=1)");
            _gpuModeCombo.Items.Add("Let Windows Decide (GpuPreference=0)");
            _gpuModeCombo.SelectedIndex = defaultGpuMode == "power_saving" ? 1 : (defaultGpuMode == "let_windows_decide" ? 2 : 0);
            Controls.Add(_gpuModeCombo);

            // Phase Checklist
            _phaseChecks = new Label[PhaseNames.Length];
            for (int i = 0; i < PhaseNames.Length; i++)
            {
                _phaseChecks[i] = new Label
                {
                    Text = "○  " + PhaseNames[i],
                    Location = new Point(32, 322 + (i * 24)),
                    AutoSize = true,
                    ForeColor = Color.FromArgb(148, 163, 184)
                };
                Controls.Add(_phaseChecks[i]);
            }

            _phaseTitleLabel = new Label
            {
                Text = "Ready to install ODS Windows Native Desktop.",
                Location = new Point(28, 476),
                AutoSize = true,
                Font = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(226, 232, 240)
            };
            Controls.Add(_phaseTitleLabel);

            _phaseDetailLabel = new Label
            {
                Text = "Click 'Install ODS Desktop' to begin setup.",
                Location = new Point(28, 498),
                Width = 650,
                Height = 20,
                ForeColor = Color.FromArgb(148, 163, 184)
            };
            Controls.Add(_phaseDetailLabel);

            _progressBar = new ProgressBar
            {
                Location = new Point(28, 524),
                Width = 470,
                Height = 32,
                Minimum = 0,
                Maximum = 100,
                Value = 0
            };
            Controls.Add(_progressBar);

            _installButton = new Button
            {
                Text = "Install ODS Desktop",
                Location = new Point(512, 523),
                Size = new Size(166, 34),
                BackColor = Color.FromArgb(14, 165, 233),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold)
            };
            _installButton.FlatAppearance.BorderSize = 0;
            _installButton.Click += OnInstallClick;
            Controls.Add(_installButton);

            _launchButton = new Button
            {
                Text = "Launch ODS",
                Location = new Point(512, 523),
                Size = new Size(166, 34),
                BackColor = Color.FromArgb(16, 185, 129),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI Semibold", 9.5f, FontStyle.Bold),
                Visible = false
            };
            _launchButton.FlatAppearance.BorderSize = 0;
            _launchButton.Click += OnLaunchClick;
            Controls.Add(_launchButton);

            // Background hardware scan on launch
            Thread hwThread = new Thread(DetectHardwareTelemetry)
            {
                IsBackground = true
            };
            hwThread.Start();
        }

        private void DetectHardwareTelemetry()
        {
            string gpuName = "CPU Only";
            int vramMb = 0;
            int ramGb = 16;
            long diskFreeGb = 50;

            try
            {
                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher("SELECT Name, AdapterRAM FROM Win32_VideoController"))
                {
                    foreach (ManagementObject obj in searcher.Get())
                    {
                        string name = obj["Name"] != null ? obj["Name"].ToString() : "";
                        long rawVram = 0;
                        if (obj["AdapterRAM"] != null && long.TryParse(obj["AdapterRAM"].ToString(), out rawVram))
                        {
                            int mb = (int)Math.Round(rawVram / (1024.0 * 1024.0));
                            if (name.IndexOf("nvidia", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                name.IndexOf("radeon", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                mb > vramMb)
                            {
                                gpuName = name;
                                vramMb = mb;
                            }
                        }
                    }
                }

                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher("SELECT TotalPhysicalMemory FROM Win32_ComputerSystem"))
                {
                    foreach (ManagementObject obj in searcher.Get())
                    {
                        long rawRam = 0;
                        if (obj["TotalPhysicalMemory"] != null && long.TryParse(obj["TotalPhysicalMemory"].ToString(), out rawRam))
                        {
                            ramGb = (int)Math.Round(rawRam / (1024.0 * 1024.0 * 1024.0));
                        }
                    }
                }

                string driveLetter = Path.GetPathRoot(_installDir);
                if (string.IsNullOrEmpty(driveLetter)) driveLetter = "C:\\";
                DriveInfo dInfo = new DriveInfo(driveLetter);
                if (dInfo.IsReady)
                {
                    diskFreeGb = dInfo.AvailableFreeSpace / (1024L * 1024L * 1024L);
                }
            }
            catch
            {
            }

            string tierDesc;
            if (vramMb >= 12000 || ramGb >= 48)
            {
                tierDesc = "Tier 2 / Pro (Qualified for Full Stack 9B model)";
            }
            else if (vramMb >= 4000)
            {
                tierDesc = "Tier 1 / Entry (Optimized for Recommended Core 2B model)";
            }
            else
            {
                tierDesc = "Tier 0 / Minimal (Optimized for Power-Saving 2B model)";
            }

            BeginInvoke((Action)(() =>
            {
                _hwSummaryLabel.Text = string.Format("Hardware: {0} ({1} MB VRAM) • {2} GB RAM • {3} GB Free", gpuName, vramMb, ramGb, diskFreeGb);
                _hwTierLabel.Text = string.Format("Classification: {0}", tierDesc);
            }));
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
            _installButton.Text = "Installing...";
            _installDirBox.Enabled = false;
            _gpuModeCombo.Enabled = false;
            _profileCombo.Enabled = false;

            _phaseTitleLabel.Text = "Starting ODS Installation Engine...";
            _phaseDetailLabel.Text = "Initializing hardware, Python runtime, and model provisioning...";

            _installDir = _installDirBox.Text.Trim();
            string mode = _gpuModeCombo.SelectedIndex == 1
                ? "power_saving"
                : (_gpuModeCombo.SelectedIndex == 2 ? "let_windows_decide" : "high_performance");

            string profile = _profileCombo.SelectedIndex == 1
                ? "full"
                : (_profileCombo.SelectedIndex == 2 ? "minimal" : "recommended");

            Thread worker = new Thread(() => RunInstallerProcess(_installDir, mode, profile))
            {
                IsBackground = true
            };
            worker.Start();
        }

        private void RunInstallerProcess(string installDir, string gpuMode, string installProfile)
        {
            string scriptPath = Program.ResolveInstallerScriptPath();
            if (string.IsNullOrEmpty(scriptPath) || !File.Exists(scriptPath))
            {
                BeginInvoke((Action)(() =>
                {
                    MessageBox.Show(
                        "Could not locate windows-desktop.ps1.\n\nPlease ensure windows-desktop.ps1 is kept in the same folder as ODS-Setup.exe.",
                        "ODS Setup Error",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    _phaseTitleLabel.Text = "Installer script not found.";
                    _phaseDetailLabel.Text = "windows-desktop.ps1 must be in the same folder as ODS-Setup.exe.";
                    _installButton.Text = "Install ODS Desktop";
                    _installButton.Enabled = true;
                    _installDirBox.Enabled = true;
                    _gpuModeCombo.Enabled = true;
                    _profileCombo.Enabled = true;
                    _isInstalling = false;
                }));
                return;
            }

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = string.Format(
                    "-NoProfile -ExecutionPolicy Bypass -File \"{0}\" -InstallDir \"{1}\" -DefaultGpuPreference {2} -InstallProfile {3}",
                    scriptPath,
                    installDir,
                    gpuMode,
                    installProfile
                ),
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8
            };

            try
            {
                using (Process proc = new Process { StartInfo = psi })
                {
                    StringBuilder stderrSb = new StringBuilder();
                    proc.ErrorDataReceived += (s, ev) =>
                    {
                        if (!string.IsNullOrEmpty(ev.Data))
                        {
                            stderrSb.AppendLine(ev.Data);
                        }
                    };

                    proc.Start();
                    proc.BeginErrorReadLine();

                    string line;
                    while ((line = proc.StandardOutput.ReadLine()) != null)
                    {
                        if (line.StartsWith("[ODS-PHASE]", StringComparison.Ordinal))
                        {
                            string json = line.Substring("[ODS-PHASE]".Length).Trim();
                            HandlePhaseJson(json);
                        }
                        else if (!string.IsNullOrWhiteSpace(line))
                        {
                            string clean = line.Trim();
                            if (clean.Length > 0 && !clean.StartsWith("{", StringComparison.Ordinal))
                            {
                                BeginInvoke((Action)(() =>
                                {
                                    _phaseDetailLabel.Text = clean.Length > 85 ? clean.Substring(0, 82) + "..." : clean;
                                }));
                            }
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
                            string err = stderrSb.ToString().Trim();
                            if (string.IsNullOrEmpty(err)) err = "Exit code " + code;
                            _phaseTitleLabel.Text = "Installation encountered an error (exit " + code + ").";
                            _phaseDetailLabel.Text = err.Length > 95 ? err.Substring(0, 92) + "..." : err;
                            _installButton.Text = "Retry Install";
                            _installButton.Enabled = true;
                            _installDirBox.Enabled = true;
                            _gpuModeCombo.Enabled = true;
                            _profileCombo.Enabled = true;
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
                    _phaseDetailLabel.Text = ex.ToString();
                    _installButton.Text = "Retry Install";
                    _installButton.Enabled = true;
                    _installDirBox.Enabled = true;
                    _gpuModeCombo.Enabled = true;
                    _profileCombo.Enabled = true;
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
                if (idx >= 0 && idx < PhaseNames.Length && idx < _phaseChecks.Length)
                {
                    string baseText = PhaseNames[idx];
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
            Match m = Regex.Match(json, "\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
            if (m.Success)
            {
                return m.Groups[1].Value.Replace("\\\\", "\\").Replace("\\\"", "\"");
            }
            return "";
        }

        private void OnLaunchClick(object sender, EventArgs e)
        {
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string exe = Path.Combine(appData, "ODS", "app", "current", "ODS.exe");
            if (File.Exists(exe))
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = exe,
                    WorkingDirectory = Path.GetDirectoryName(exe),
                    UseShellExecute = true
                });
                Close();
            }
            else
            {
                MessageBox.Show(
                    "Could not locate ODS.exe at:\n" + exe,
                    "ODS Launch",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
            }
        }
    }
}
