// SPDX-License-Identifier: Apache-2.0
// Launch the shipped entrypoint in a separate process, without starting inference.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;

static class EntrypointTests
{
    delegate bool ChildWindow(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, ChildWindow callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int maximum);
    static int checks;
    static void Check(bool condition, string message)
    { if (!condition) throw new Exception(message); checks++; Console.WriteLine("PASS " + message); }
    static IntPtr WaitForWindow(Process process, string title)
    {
        var clock = Stopwatch.StartNew();
        while (clock.ElapsedMilliseconds < 15000)
        {
            process.Refresh();
            if (process.HasExited) throw new Exception("Preview exited before opening " + title + "; code " + process.ExitCode);
            if (process.MainWindowHandle != IntPtr.Zero && process.MainWindowTitle == title) return process.MainWindowHandle;
            Thread.Sleep(50);
        }
        throw new TimeoutException("Preview did not open " + title + ". Current title: " + process.MainWindowTitle);
    }
    static string DialogText(IntPtr window)
    {
        var result = new StringBuilder();
        EnumChildWindows(window, (child, parameter) =>
        {
            var text = new StringBuilder(2048); GetWindowText(child, text, text.Capacity);
            result.AppendLine(text.ToString()); return true;
        }, IntPtr.Zero);
        return result.ToString();
    }
    static Process Open(string executable)
    {
        return Process.Start(new ProcessStartInfo(executable) { UseShellExecute = false,
            WorkingDirectory = Path.GetDirectoryName(executable) });
    }
    static void Close(Process process)
    {
        if (process.HasExited) return;
        if (!process.CloseMainWindow() || !process.WaitForExit(10000))
            throw new Exception("The preview did not close normally.");
        Check(process.ExitCode == 0, "real entrypoint exits successfully after its window closes");
    }
    static void Cleanup(Process process)
    {
        // Only the retained handle to a process this test created; never PID/name scans.
        if (!process.HasExited) { process.Kill(); process.WaitForExit(5000); }
    }
    public static int Run(string root, string executable, string manifest)
    {
        string mutexName = "Local\\ODSDesktopPreview-" + WindowsIdentity.GetCurrent().User.Value;
        Mutex existing;
        if (Mutex.TryOpenExisting(mutexName, out existing))
        {
            existing.Dispose();
            throw new InvalidOperationException("Close your existing ODS Windows Preview before running entrypoint tests.");
        }
        string package = Path.Combine(root, "entrypoint package with spaces and Unicode đ");
        Directory.CreateDirectory(package);
        string app = Path.Combine(package, "ODS Windows Preview.exe"), pin = Path.Combine(package, "bundle.json");
        File.Copy(executable, app); File.Copy(manifest, pin);
        using (var first = Open(app))
        {
            try
            {
                WaitForWindow(first, "ODS \u2014 Windows Preview");
                Check(true, "real EXE entrypoint opens from a Unicode path without inference or downloads");
                using (var second = Open(app))
                {
                    try
                    {
                        var dialog = WaitForWindow(second, "ODS");
                        Check(DialogText(dialog).Contains("ODS Windows Preview is already open."), "second EXE explains the existing instance");
                        Close(second);
                        Check(!first.HasExited, "closing the duplicate leaves the original preview running");
                    }
                    finally { Cleanup(second); }
                }
                Close(first);
            }
            finally { Cleanup(first); }
        }
        File.WriteAllText(pin, "{\"Model\":null}");
        using (var broken = Open(app))
        {
            try
            {
                var dialog = WaitForWindow(broken, "ODS could not open");
                Check(DialogText(dialog).Contains("Extract the complete ZIP again."), "real entrypoint gives recovery instructions for an incomplete package");
                Close(broken);
            }
            finally { Cleanup(broken); }
        }
        File.Copy(manifest, pin, true);
        using (var recovered = Open(app))
        {
            try
            {
                WaitForWindow(recovered, "ODS \u2014 Windows Preview");
                Check(true, "real entrypoint can reopen after normal shutdown and package recovery");
                Close(recovered);
            }
            finally { Cleanup(recovered); }
        }
        return checks;
    }
}
