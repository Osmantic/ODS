// SPDX-License-Identifier: Apache-2.0
// Only operates windows belonging to this test's installer process tree.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;

static class InstallerUiTests
{
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    static HashSet<int> Children(int root)
    {
        var owned = new HashSet<int> { root };
        var parents = new Dictionary<int, int>();
        using (var query = new ManagementObjectSearcher("SELECT ProcessId,ParentProcessId FROM Win32_Process"))
        using (var rows = query.Get())
            foreach (ManagementObject row in rows) using (row) parents[Convert.ToInt32(row["ProcessId"])] = Convert.ToInt32(row["ParentProcessId"]);
        bool changed;
        do { changed = false; foreach (var pair in parents) if (owned.Contains(pair.Value)) changed |= owned.Add(pair.Key); } while (changed);
        return owned;
    }
    static AutomationElement Control(AutomationElement root, ControlType type, string name)
    {
        var controls = root.FindAll(TreeScope.Descendants, new PropertyCondition(AutomationElement.ControlTypeProperty, type));
        foreach (AutomationElement control in controls)
            if (control.Current.IsEnabled && !control.Current.IsOffscreen && control.Current.Name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0) return control;
        return null;
    }
    static void Press(AutomationElement control)
    {
        object pattern;
        if (control.TryGetCurrentPattern(InvokePattern.Pattern, out pattern)) ((InvokePattern)pattern).Invoke();
        else if (control.TryGetCurrentPattern(SelectionItemPattern.Pattern, out pattern)) ((SelectionItemPattern)pattern).Select();
        else
        {
            int handle = control.Current.NativeWindowHandle;
            if (handle == 0) throw new InvalidOperationException("The owned installer control cannot be invoked.");
            SendMessage(new IntPtr(handle), 0x00F5, IntPtr.Zero, IntPtr.Zero); // BM_CLICK, no global keyboard/mouse.
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        DateTime started = DateTime.Now;
        bool cancel = Array.IndexOf(args, "--cancel") >= 0;
        bool prerequisite = Array.IndexOf(args, "--allow-prerequisite") >= 0;
        bool retentionCancel = Array.IndexOf(args, "--uninstall-retention-cancel") >= 0;
        var info = new ProcessStartInfo(args[0], (retentionCancel ? "/NORESTART" : "/SP- /DIR=\"" + args[1] + "\"") + " /LOG=\"" + args[2] + "\"") { UseShellExecute = false };
        using (var installer = Process.Start(info))
        {
            try
            {
                bool finished = false;
                var clock = Stopwatch.StartNew();
                bool cancelled = false;
                while (clock.Elapsed.TotalSeconds < (prerequisite ? 900 : 90) && !installer.HasExited)
                {
                    var owned = Children(installer.Id);
                    var windows = AutomationElement.RootElement.FindAll(TreeScope.Children, new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Window));
                    foreach (AutomationElement window in windows)
                    {
                        try
                        {
                        if (!owned.Contains(window.Current.ProcessId)) continue;
                        if (retentionCancel)
                        {
                            var notice = Control(window, ControlType.Text, "will be kept in:");
                            if (notice != null)
                            {
                                string data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ODS Desktop Preview");
                                if (!notice.Current.Name.Contains(data) || !notice.Current.Name.Contains("1.28 GB"))
                                    throw new Exception("Uninstall did not identify the retained data folder and model size.");
                                var no = Control(window, ControlType.Button, "No");
                                if (no != null) { Press(no); cancelled = true; }
                            }
                            continue;
                        }
                        if (cancel)
                        {
                            var stop = Control(window, ControlType.Button, cancelled ? "Yes" : "Cancel");
                            if (stop != null) { Press(stop); cancelled = true; }
                            continue;
                        }
                        if (prerequisite && Control(window, ControlType.Text, "ODS needs Microsoft WebView2") != null)
                        {
                            var yes = Control(window, ControlType.Button, "Yes");
                            if (yes != null) Press(yes);
                            continue;
                        }
                        var accept = Control(window, ControlType.RadioButton, "I accept the agreement");
                        if (accept != null) Press(accept);
                        var button = Control(window, ControlType.Button, "Finish");
                        if (button != null) { Press(button); finished = true; break; }
                        button = Control(window, ControlType.Button, "Install") ?? Control(window, ControlType.Button, "Next");
                        if (button != null) Press(button);
                        }
                        catch (ElementNotAvailableException)
                        {
                            // An enumerated window/control may close between
                            // polling and inspection. Retry the next snapshot;
                            // ownership and the overall deadline still apply.
                        }
                    }
                    Thread.Sleep(300);
                }
                if (!installer.HasExited) throw new TimeoutException("The actual installer wizard did not finish. Check its private log.");
                if (retentionCancel)
                {
                    if (!cancelled || !File.Exists(Path.Combine(args[1], "ODS Windows Preview.exe")))
                        throw new Exception("Uninstall retention cancellation did not preserve the installed application.");
                    Console.WriteLine("CANCELLED retention notice with exact data path and model size"); return 0;
                }
                if (cancel)
                {
                    if (!cancelled || installer.ExitCode != 2) throw new Exception("Setup did not acknowledge ordinary pre-install cancellation.");
                    Console.WriteLine("CANCELLED before installation"); return 0;
                }
                if (installer.ExitCode != 0 || !finished) throw new Exception("The graphical installer did not complete successfully.");
                string expected = Path.Combine(Path.GetFullPath(args[1]), "ODS Windows Preview.exe");
                clock.Restart();
                while (clock.Elapsed.TotalSeconds < 20)
                {
                    foreach (var app in Process.GetProcessesByName("ODS Windows Preview"))
                    {
                        using (app)
                        {
                            if (app.HasExited || app.StartTime < started || app.MainModule.FileName != expected) continue;
                            app.Refresh();
                            if (app.MainWindowHandle != IntPtr.Zero && app.MainWindowTitle == "ODS \u2014 Windows Preview")
                            { Console.WriteLine("APPLICATION_PID=" + app.Id); return 0; }
                        }
                    }
                    Thread.Sleep(100);
                }
                throw new Exception("The installer's Finish action did not launch its exact installed EXE.");
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error);
                // Request ordinary cancellation only on the retained installer.
                // Never kill an unrelated browser, updater or Windows service.
                if (!installer.HasExited) installer.CloseMainWindow();
                return 1;
            }
        }
    }
}
