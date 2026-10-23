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
    [DllImport("user32.dll", SetLastError = true)] static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
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
        // Inno's legacy provider can expose its native controls as Pane rather
        // than Button/Text. Match only the known native classes in that case.
        var controls = root.FindAll(TreeScope.Descendants, Condition.TrueCondition);
        foreach (AutomationElement control in controls)
        {
            var item = control.Current;
            bool native = type == ControlType.Button && (item.ClassName == "TNewButton" || item.ClassName == "TButton" || item.ClassName == "Button") ||
                type == ControlType.Text && (item.ClassName == "TNewStaticText" || item.ClassName == "Static") ||
                type == ControlType.RadioButton && (item.ClassName == "TNewRadioButton" || item.ClassName == "TRadioButton");
            if ((item.ControlType == type || native) && item.ProcessId == root.Current.ProcessId &&
                item.IsEnabled && !item.IsOffscreen && item.Name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0) return control;
        }
        return null;
    }
    static void Press(AutomationElement control)
    {
        var item = control.Current;
        int handle = item.NativeWindowHandle;
        if (handle != 0 && (item.ClassName == "TNewButton" || item.ClassName == "TButton" || item.ClassName == "Button" ||
            item.ClassName == "TNewRadioButton" || item.ClassName == "TRadioButton"))
        {
            uint owner;
            GetWindowThreadProcessId(new IntPtr(handle), out owner);
            if (owner != item.ProcessId || !PostMessage(new IntPtr(handle), 0x00F5, IntPtr.Zero, IntPtr.Zero))
                throw new InvalidOperationException("The owned native installer control could not be invoked.");
            return; // BM_CLICK asynchronously: a modal prompt must not block the driver.
        }
        object pattern;
        if (control.TryGetCurrentPattern(InvokePattern.Pattern, out pattern)) ((InvokePattern)pattern).Invoke();
        else if (control.TryGetCurrentPattern(SelectionItemPattern.Pattern, out pattern)) ((SelectionItemPattern)pattern).Select();
        else
        {
            throw new InvalidOperationException("The owned installer control cannot be invoked.");
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        DateTime started = DateTime.Now;
        bool cancel = Array.IndexOf(args, "--cancel") >= 0;
        bool prerequisite = Array.IndexOf(args, "--allow-prerequisite") >= 0;
        bool declinePrerequisite = Array.IndexOf(args, "--decline-prerequisite") >= 0;
        bool expectedPrerequisiteFailure = Array.IndexOf(args, "--expect-prerequisite-failure") >= 0;
        int recoveryIndex = Array.IndexOf(args, "--recover-prerequisite");
        string recoveryState = recoveryIndex < 0 ? null : Path.GetFullPath(args[recoveryIndex + 1]);
        if (recoveryState != null && !recoveryState.StartsWith(Path.GetDirectoryName(Path.GetFullPath(args[1])) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Prerequisite recovery must use the private installer fixture.");
        bool retentionCancel = Array.IndexOf(args, "--uninstall-retention-cancel") >= 0;
        var info = new ProcessStartInfo(args[0], (retentionCancel ? "/NORESTART" : "/SP- /DIR=\"" + args[1] + "\"") + " /LOG=\"" + args[2] + "\"") { UseShellExecute = false };
        using (var installer = Process.Start(info))
        {
            try
            {
                bool finished = false;
                var clock = Stopwatch.StartNew();
                bool cancelled = false;
                bool prerequisiteFailure = false, cancelPrerequisite = false;
                while (clock.Elapsed.TotalSeconds < (prerequisite && !expectedPrerequisiteFailure && recoveryState == null ? 900 : 90) && !installer.HasExited)
                {
                    var owned = Children(installer.Id);
                    var windows = AutomationElement.RootElement.FindAll(TreeScope.Children, Condition.TrueCondition);
                    foreach (AutomationElement window in windows)
                    {
                        try
                        {
                        if (!owned.Contains(window.Current.ProcessId)) continue;
                        if (cancelPrerequisite)
                        {
                            var stop = Control(window, ControlType.Button, "Yes") ?? Control(window, ControlType.Button, "Cancel");
                            if (stop != null) { Press(stop); cancelled = true; }
                            continue;
                        }
                        if ((expectedPrerequisiteFailure || recoveryState != null) && !prerequisiteFailure)
                        {
                            string failure = declinePrerequisite ? "WebView2 installation was cancelled" : "Microsoft WebView2 did not finish installing";
                            if (Control(window, ControlType.Text, failure) != null)
                            {
                                if (File.Exists(Path.Combine(args[1], "ODS Windows Preview.exe")))
                                    throw new InvalidOperationException("Prerequisite failure copied application files before acceptance.");
                                prerequisiteFailure = true;
                                Console.WriteLine("PREREQUISITE_FAILURE_OBSERVED=True");
                                if (recoveryState == null) { cancelPrerequisite = true; continue; }
                                File.WriteAllText(recoveryState, "success");
                                var back = Control(window, ControlType.Button, "Back");
                                if (back == null) throw new InvalidOperationException("Prerequisite failure did not expose safe retry navigation.");
                                Press(back);
                                continue;
                            }
                        }
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
                            var yes = Control(window, ControlType.Button, declinePrerequisite ? "No" : "Yes");
                            if (yes != null) Press(yes);
                            continue;
                        }
                        var accept = Control(window, ControlType.RadioButton, "I accept the agreement");
                        if (accept != null) Press(accept);
                        var button = Control(window, ControlType.Button, "Finish");
                        if (button != null)
                        {
                            if (expectedPrerequisiteFailure) throw new InvalidOperationException("Setup bypassed the required prerequisite refusal.");
                            if (recoveryState != null && !prerequisiteFailure) throw new InvalidOperationException("Recovery did not exercise an initial prerequisite failure.");
                            Press(button); finished = true; break;
                        }
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
                if (expectedPrerequisiteFailure)
                {
                    if (!prerequisiteFailure || !cancelled || installer.ExitCode == 0)
                        throw new InvalidOperationException("Expected prerequisite refusal/cancellation did not settle.");
                    Console.WriteLine("REFUSED prerequisite; setup exit=" + installer.ExitCode); return 0;
                }
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
                var cleanup = Stopwatch.StartNew();
                while (!installer.HasExited && cleanup.Elapsed.TotalSeconds < 5)
                {
                    var owned = Children(installer.Id);
                    foreach (AutomationElement window in AutomationElement.RootElement.FindAll(TreeScope.Children, Condition.TrueCondition))
                    {
                        try
                        {
                            if (!owned.Contains(window.Current.ProcessId)) continue;
                            bool componentPrompt = Control(window, ControlType.Text, "ODS needs Microsoft WebView2") != null;
                            var stop = componentPrompt ? Control(window, ControlType.Button, "No") :
                                Control(window, ControlType.Button, "Cancel") ?? Control(window, ControlType.Button, "Yes") ?? Control(window, ControlType.Button, "OK");
                            if (stop != null) Press(stop);
                        }
                        catch (ElementNotAvailableException) { }
                    }
                    Thread.Sleep(100);
                }
                return 1;
            }
        }
    }
}
