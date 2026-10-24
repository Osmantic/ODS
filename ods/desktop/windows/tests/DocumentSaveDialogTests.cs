// SPDX-License-Identifier: Apache-2.0
// Drives only this process's Portal-owned production SaveFileDialog. No keys,
// mouse input, dialog substitution or production download overrides.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Automation;

static class DocumentSaveDialogTests
{
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window, uint command);
    delegate bool WindowCallback(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] static extern bool EnumWindows(WindowCallback callback, IntPtr parameter);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder value, int maximum);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder value, int maximum);
    [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr window);
    [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", SetLastError = true)] static extern IntPtr SelectText(
        IntPtr window, uint message, IntPtr wparam, IntPtr lparam, uint flags, uint timeout, out UIntPtr result);
    [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr ReplaceText(
        IntPtr window, uint message, IntPtr wparam, string text, uint flags, uint timeout, out UIntPtr result);
    [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr ReadText(
        IntPtr window, uint message, IntPtr wparam, StringBuilder text, uint flags, uint timeout, out UIntPtr result);
    static string NativeFilename(IntPtr edit)
    {
        UIntPtr result; var text = new StringBuilder(2048);
        if (ReadText(edit, 0x000D, new IntPtr(text.Capacity), text, 2, 1000, out result) == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return text.ToString();
    }
    static IntPtr EditFilename(AutomationElement filename, int processId, string target)
    {
        IntPtr edit = new IntPtr(filename.Current.NativeWindowHandle);
        uint actual; GetWindowThreadProcessId(edit, out actual);
        var kind = new StringBuilder(256); GetClassName(edit, kind, kind.Capacity);
        if (edit == IntPtr.Zero || actual != processId || GetDlgCtrlID(edit) != 1001 || kind.ToString() != "Edit")
            throw new InvalidOperationException("The filename HWND is not this PID's native filename edit.");
        UIntPtr result;
        if (SelectText(edit, 0x00B1, IntPtr.Zero, new IntPtr(-1), 2, 1000, out result) == IntPtr.Zero ||
            ReplaceText(edit, 0x00C2, new IntPtr(1), target, 2, 1000, out result) == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error());
        if (NativeFilename(edit) != target) throw new InvalidOperationException("The native filename edit did not accept the private target.");
        return edit;
    }
    static bool OwnedBy(IntPtr dialog, IntPtr owner)
    {
        for (int depth = 0; depth < 12 && dialog != IntPtr.Zero; depth++)
        {
            dialog = GetWindow(dialog, 4); // GW_OWNER; never changes focus/input.
            if (dialog == owner) return true;
        }
        return false;
    }
    static AutomationElement FindDialog(int processId, IntPtr owner, string title)
    {
        IntPtr found = IntPtr.Zero;
        // Native owned dialogs can be UIA children of their WPF owner rather
        // than root children. Enumerate HWND metadata first, then attach UIA
        // only after exact PID, class, title and owner checks pass.
        EnumWindows((window, parameter) =>
        {
            uint actual; GetWindowThreadProcessId(window, out actual);
            if (actual != processId || !OwnedBy(window, owner)) return true;
            var name = new StringBuilder(256); var kind = new StringBuilder(256);
            GetWindowText(window, name, name.Capacity); GetClassName(window, kind, kind.Capacity);
            if (name.ToString() != title || kind.ToString() != "#32770") return true;
            found = window; return false;
        }, IntPtr.Zero);
        return found == IntPtr.Zero ? null : AutomationElement.FromHandle(found);
    }
    static AutomationElement Control(AutomationElement dialog, ControlType type, string id)
    {
        var element = dialog.FindFirst(TreeScope.Descendants, new AndCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, type),
            new PropertyCondition(AutomationElement.AutomationIdProperty, id)));
        return element != null && element.Current.IsEnabled ? element : null;
    }
    public static Task ChooseAsync(IntPtr owner, string target, bool accept, CancellationToken token,
        string title = "Save document", string suggested = "document.md", int holdMilliseconds = 0)
    {
        if (owner == IntPtr.Zero || (title != "Save document" && title != "Save conversation") ||
            !Path.IsPathRooted(target) || File.Exists(target) || !Directory.Exists(Path.GetDirectoryName(target)) ||
            holdMilliseconds < 0 || holdMilliseconds > 2500)
            throw new ArgumentException("Save-dialog tests require an existing private directory and an unused absolute file path.");
        int processId = Process.GetCurrentProcess().Id;
        // UIAutomation must run off the WPF dispatcher: ShowDialog pumps that
        // dispatcher while the separate worker sets/invokes its owned controls.
        return Task.Run(() =>
        {
            var clock = Stopwatch.StartNew();
            int stale = 0;
            while (clock.Elapsed < TimeSpan.FromSeconds(30))
            {
                token.ThrowIfCancellationRequested();
                try
                {
                    var dialog = FindDialog(processId, owner, title);
                    if (dialog != null)
                    {
                        var filename = Control(dialog, ControlType.Edit, "1001");
                        var button = Control(dialog, ControlType.Button, accept ? "1" : "2");
                        object raw;
                        if (filename != null && button != null && filename.TryGetCurrentPattern(ValuePattern.Pattern, out raw))
                        {
                            var value = (ValuePattern)raw;
                            string shown = Path.GetFileName(value.Current.Value);
                            // Explorer may hide a registered extension, and a
                            // newly opened filename control can still be empty.
                            if (shown != suggested && shown != Path.GetFileNameWithoutExtension(suggested))
                            { if (token.WaitHandle.WaitOne(75)) token.ThrowIfCancellationRequested(); continue; }
                            filename.SetFocus();
                            // Shell's UIA ValuePattern can echo a path without
                            // committing its internal filename. Edit only the
                            // validated owned HWND so normal edit notifications
                            // reach the real dialog; no global keyboard input.
                            IntPtr edit = EditFilename(filename, processId, target);
                            if (!String.Equals(value.Current.Value, target, StringComparison.Ordinal))
                                throw new InvalidOperationException("The owned save dialog did not accept the private fixture path.");
                            if (!button.TryGetCurrentPattern(InvokePattern.Pattern, out raw))
                                throw new InvalidOperationException("The owned save dialog button cannot be invoked.");
                            token.ThrowIfCancellationRequested();
                            // Like a real click, move focus out of Explorer's
                            // filename edit so its internal name commits before
                            // invoking Save. ValuePattern's echo alone is not
                            // proof that the dialog accepted that filename.
                            button.SetFocus();
                            if (value.Current.Value != target || NativeFilename(edit) != target)
                                throw new InvalidOperationException("The private filename changed when focus left its native edit.");
                            if (holdMilliseconds > 0 && token.WaitHandle.WaitOne(holdMilliseconds)) token.ThrowIfCancellationRequested();
                            if (holdMilliseconds > 0) Console.WriteLine("SAVE DIALOG held " + holdMilliseconds + "ms before owner acceptance");
                            ((InvokePattern)raw).Invoke();
                            return;
                        }
                    }
                }
                catch (ElementNotAvailableException)
                {
                    // Only a disappearing native dialog/control is retried;
                    // PID, owner and the overall deadline remain mandatory.
                    if (++stale > 3) throw;
                }
                if (token.WaitHandle.WaitOne(75)) token.ThrowIfCancellationRequested();
            }
            throw new TimeoutException("The current PID's Portal-owned Save document dialog did not expose its filename and choice controls.");
        }, token);
    }
}
